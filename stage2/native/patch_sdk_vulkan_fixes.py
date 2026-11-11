#!/usr/bin/env python3
"""tt-android changes to the ReXGlue SDK v0.10.0 sources.

Stage 2 runs this on a FRESH copy of the SDK every build (see tt_android_stage2.sh), so
every change here is applied exactly once to unmodified sources: no idempotency guards,
no repair steps. To remove a change, delete its section; the next build won't have it.

FIXES (each measured to help; see HANDOFF.md for the history)
  F1  #456  texture exponent bias read from fetch-constant word 3
  F2  #507  presenter remembers its swapchain format (no paint-pipeline rebuild every frame)
  F3  #441  write-watch faults don't re-parse /proc/self/maps                        ~+30%
  F4        multi-object waits sleep instead of busy-spinning (audio worker)
  F5        GPU progress poll uses vkGetFenceStatus (zero-timeout waits are slow on KGSL)
  F6        GPU progress from a memory marker instead of driver fence queries   ~29->36 FPS
  F7        presenter's submission tracker doesn't query very young fences (KGSL ~13 ms)
  F8        presenter: swapchain acquire waited on the CPU; the UI thread doesn't wait for
            its paint (screen shows every game frame: ~22 -> ~44 repaints/s)
  F9        skip re-converting textures whose guest bytes didn't change          ~+1 FPS
            (and don't hash textures that will reload anyway: less CPU)
  F10       skip two depth-only passes with no visible effect (640/1120 wide)     ~+2 FPS
  F11       skip a full-size pass that writes neither colour nor depth (~96 draws) ~+0.5 FPS
  F12       skip render-target copies (EDRAM transfers between passes, ~26/frame)  ~+1.4 FPS
  F13       stereo sound mix by the SDK instead of Android's 5.1 downmix, rear (echo)
            channels at 0.25 (clearer, drier ball hits)
  F14       screen repainted once per new game frame (no repeated repaints)
  (F10-F12 are switched off together by TT_DRAW_ALL_PASSES=1: launcher "Hidden passes")

DIAGNOSTICS (counters read by tt_profiler.cpp via dlsym; cheap atomic adds)
  D1  tt_frame_stats  [0] guest vblanks [1] CP wait-for-memory sleeps [2] their ns [3] swaps
  D2  tt_work_stats   [0] draws [1] resolves [2] texture loads [3] texture bytes
                      [6] upload calls [7] upload bytes [8] draws skipped by F10/F11
  D3  tt_tex_stats    [0] texture loads asked [1] skipped (unchanged) [2] GPU-written
                      [3] bytes hashed
  D4  tt_present_stats [0] new game images [1] screen paints (F14)

  F15 uploads recorded in front of the open render pass instead of cutting it (v84: render
      passes 256 -> 68 per frame, ~46 -> ~52 FPS). Off with TT_DRAW_ALL_PASSES.
      tt_work_stats [9] passes begun [10] uploads moved [11] uploads that cut the pass
"""
import re
import sys
from pathlib import Path

sdk = Path(sys.argv[1])
done = []


def edit(rel, *pairs, includes=(), prelude=""):
    """Replace each (old, new) exactly once in sdk/rel; add includes and a prelude
    (placed before the file's first namespace)."""
    path = sdk / rel
    text = path.read_text()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit(f"{rel}: patch point not found (SDK changed?):\n{old.strip()[:200]}")
        text = text.replace(old, new)
    if prelude:
        ns = text.find("\nnamespace")
        if ns < 0:
            sys.exit(f"{rel}: no namespace to put the prelude before")
        text = text[:ns] + "\n" + prelude.rstrip("\n") + "\n" + text[ns:]
    for inc in includes:
        if inc not in text:
            text = text.replace("#include", inc + "\n#include", 1)
    path.write_text(text)


CP = "src/graphics/vulkan/command_processor.cpp"
PRESENTER = "src/ui/vulkan/vulkan_presenter.cpp"

# ============================================================================ FIXES

# ---- F1 (#456) The SPIR-V translator took the texture exponent bias from fetch-constant
#      word 4 (the LOD-bias bits) instead of word 3, darkening textures with a LOD bias.
edit("src/graphics/pipeline/shader/spirv_translator_fetch.cpp", (
    """        // Apply the exponent bias from the bits 13:18 of the fetch constant
        // word 4.
        spv::Id result_exponent_bias = builder_->createBinBuiltinCall(
            type_float_, ext_inst_glsl_std_450_, GLSLstd450Ldexp, const_float_1_,
            builder_->createTriOp(spv::OpBitFieldSExtract, type_int_, fetch_constant_word_4_signed,
                                  builder_->makeUintConstant(13), builder_->makeUintConstant(6)));""",
    """        // Apply the exponent bias from the bits 13:18 of the fetch constant
        // word 3 (exp_adjust). [tt-android: rexglue-sdk #456, was word 4]
        id_vector_temp_.clear();
        id_vector_temp_.push_back(const_int_0_);
        id_vector_temp_.push_back(
            builder_->makeIntConstant(int((fetch_constant_word_0_index + 3) >> 2)));
        id_vector_temp_.push_back(
            builder_->makeIntConstant(int((fetch_constant_word_0_index + 3) & 3)));
        spv::Id fetch_constant_word_3_signed = builder_->createUnaryOp(
            spv::OpBitcast, type_int_,
            builder_->createLoad(builder_->createAccessChain(spv::StorageClassUniform,
                                                             uniform_fetch_constants_,
                                                             id_vector_temp_),
                                 spv::NoPrecision));
        spv::Id result_exponent_bias = builder_->createBinBuiltinCall(
            type_float_, ext_inst_glsl_std_450_, GLSLstd450Ldexp, const_float_1_,
            builder_->createTriOp(spv::OpBitFieldSExtract, type_int_, fetch_constant_word_3_signed,
                                  builder_->makeUintConstant(13), builder_->makeUintConstant(6)));"""))
done.append("F1 texture exponent bias from fetch word 3 (#456)")

# ---- F2 (#507) The guest-output paint pipeline was recreated (and the previous paint
#      awaited) every frame because swapchain_format was never stored.
text = (sdk / PRESENTER).read_text()
m = re.search(r"\n(\s*)(\w[\w\.\[\]>-]*\.swapchain_pipeline)\s*=\s*CreateGuestOutputPaintPipeline\([^;]*\);\n",
              text)
if not m:
    sys.exit("F2: presenter paint-pipeline assignment not found")
indent, lhs = m.group(1), m.group(2)
owner = lhs.rsplit(".swapchain_pipeline", 1)[0]
text = (text[:m.end()] + f"{indent}if ({lhs} != VK_NULL_HANDLE) {{  // tt-android: rexglue-sdk #507\n"
        f"{indent}  {owner}.swapchain_format = paint_context_.swapchain_render_pass_format;\n"
        f"{indent}}}\n" + text[m.end():])
(sdk / PRESENTER).write_text(text)
done.append("F2 presenter keeps its swapchain format (#507)")

# ---- F3 (#441) Every write-watch fault re-read /proc/self/maps under the global lock.
#      The access-violation callback re-checks the authoritative watch state anyway.
edit("src/system/mmio_handler.cpp", (
    """    auto lock = global_critical_region_.Acquire();
    memory::PageAccess cur_access;
    size_t page_length = memory::page_size();
    memory::QueryProtect(fault_host_address, page_length, cur_access);
    if (cur_access != memory::PageAccess::kNoAccess &&
        (!is_write || cur_access != memory::PageAccess::kReadOnly)) {
      // Another thread has cleared this watch. Abort.
      return true;
    }""",
    """    auto lock = global_critical_region_.Acquire();
    // tt-android: rexglue-sdk #441 - no /proc/self/maps recheck here; the callback below
    // checks the authoritative write-watch state."""))
done.append("F3 write-watch faults skip the /proc/self/maps recheck (#441)")

# ---- F4 Multi-object waits rounded their last poll slice down to sleep_for(0) and spun;
#      the audio worker waits this way and burned a full core.
edit("src/core/threading_posix.cpp", (
    """      if (timeout == std::chrono::milliseconds::max()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      } else {
        auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - now);
        auto sleep_time = std::min(remaining, std::chrono::milliseconds(1));
        std::this_thread::sleep_for(sleep_time);
      }""",
    """      // tt-android: sleep in microseconds; the millisecond cast spun on sleep_for(0).
      constexpr auto kPollSlice = std::chrono::microseconds(500);
      if (timeout == std::chrono::milliseconds::max()) {
        std::this_thread::sleep_for(kPollSlice);
      } else {
        auto remaining = std::chrono::duration_cast<std::chrono::microseconds>(end_time - now);
        if (remaining < std::chrono::microseconds(50)) {
          return std::make_pair<WaitResult, size_t>(WaitResult::kTimeout, 0);
        }
        std::this_thread::sleep_for(std::min(remaining, kPollSlice));
      }"""), (
    """      if (!all_locked) {
        locks.clear();
        std::this_thread::yield();
        continue;
      }""",
    """      if (!all_locked) {
        locks.clear();
        std::this_thread::sleep_for(std::chrono::microseconds(20));  // tt-android: was yield()
        continue;
      }"""))
done.append("F4 multi-object waits sleep instead of spinning")

# ---- F5 + F6 GPU progress. On KGSL a fence status query / zero-timeout wait on a pending
#      fence costs ~13 ms. F5: the "how far is the GPU" poll uses vkGetFenceStatus.
#      F6: each submission ends by writing its number into a small host-visible buffer
#      (after a full barrier); the CPU reads that instead of asking the driver. Retired
#      fences are destroyed a few submissions later rather than reset and reused.
MARKER = """
// tt-android F6: GPU progress marker
namespace rex::graphics::vulkan {
namespace tt_marker {
bool init_failed = false;
VkBuffer buffer = VK_NULL_HANDLE;
VkDeviceMemory memory = VK_NULL_HANDLE;
volatile uint32_t* mapped = nullptr;
PFN_vkCmdFillBuffer fill = nullptr;
std::deque<std::pair<uint64_t, VkFence>> graveyard;

bool Ready(const VulkanCommandProcessor& cp) {
  if (mapped) return true;
  if (init_failed) return false;
  init_failed = true;
  const ui::vulkan::VulkanDevice* device = cp.GetVulkanDevice();
  const auto& dfn = device->functions();
  fill = reinterpret_cast<PFN_vkCmdFillBuffer>(
      device->vulkan_instance()->functions().vkGetDeviceProcAddr(device->device(),
                                                                 "vkCmdFillBuffer"));
  if (!fill) return false;
  if (!ui::vulkan::util::CreateDedicatedAllocationBuffer(
          device, 256, VK_BUFFER_USAGE_TRANSFER_DST_BIT, ui::vulkan::util::MemoryPurpose::kReadback,
          buffer, memory)) {
    return false;
  }
  void* ptr = nullptr;
  if (dfn.vkMapMemory(device->device(), memory, 0, VK_WHOLE_SIZE, 0, &ptr) != VK_SUCCESS || !ptr) {
    return false;
  }
  mapped = static_cast<volatile uint32_t*>(ptr);
  mapped[0] = 0;
  init_failed = false;
  REXLOG_INFO("tt: GPU progress marker active");
  return true;
}

// Highest submission the GPU has fully finished, according to the marker.
uint64_t Done(const VulkanCommandProcessor& cp, uint64_t completed) {
  const ui::vulkan::VulkanDevice* device = cp.GetVulkanDevice();
  VkMappedMemoryRange range = {};
  range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
  range.memory = memory;
  range.size = VK_WHOLE_SIZE;
  device->functions().vkInvalidateMappedMemoryRanges(device->device(), 1, &range);
  const uint32_t low = mapped[0];
  uint64_t value = (completed & ~uint64_t(0xFFFFFFFFu)) | low;
  if (value < completed) value += uint64_t(1) << 32;
  return value;
}
}  // namespace tt_marker
}  // namespace rex::graphics::vulkan
"""
edit(CP, (
    # F5
    "    VkResult fence_status =\n"
    "        dfn.vkWaitForFences(device, 1, &submissions_in_flight_fences_[fences_awaited], VK_TRUE, 0);\n",
    "    VkResult fence_status =  // tt-android F5: status query, not a zero-timeout wait\n"
    "        dfn.vkGetFenceStatus(device, submissions_in_flight_fences_[fences_awaited]);\n"), (
    # F6: write the marker at the end of every submission
    "    if (dfn.vkEndCommandBuffer(command_buffer.buffer) != VK_SUCCESS) {\n",
    "    if (tt_marker::Ready(*this)) {  // tt-android F6: GPU progress marker\n"
    "      VkMemoryBarrier before = {};\n"
    "      before.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;\n"
    "      before.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;\n"
    "      before.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
    "      dfn.vkCmdPipelineBarrier(command_buffer.buffer, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,\n"
    "                               VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &before, 0, nullptr, 0,\n"
    "                               nullptr);\n"
    "      tt_marker::fill(command_buffer.buffer, tt_marker::buffer, 0, 4,\n"
    "                      uint32_t(GetCurrentSubmission()));\n"
    "      VkMemoryBarrier after = {};\n"
    "      after.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;\n"
    "      after.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
    "      after.dstAccessMask = VK_ACCESS_HOST_READ_BIT;\n"
    "      dfn.vkCmdPipelineBarrier(command_buffer.buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,\n"
    "                               VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &after, 0, nullptr, 0,\n"
    "                               nullptr);\n"
    "    }\n"
    "    if (dfn.vkEndCommandBuffer(command_buffer.buffer) != VK_SUCCESS) {\n"), (
    # F6: completion from the marker, before any driver fence wait / poll
    "  size_t fences_awaited = 0;\n",
    "  size_t fences_awaited = 0;\n"
    "  if (tt_marker::Ready(*this)) {  // tt-android F6\n"
    "    uint64_t done = tt_marker::Done(*this, submission_completed_);\n"
    "    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);\n"
    "    while (await_submission > done && std::chrono::steady_clock::now() < deadline) {\n"
    "      std::this_thread::sleep_for(std::chrono::microseconds(50));\n"
    "      done = tt_marker::Done(*this, submission_completed_);\n"
    "    }\n"
    "    if (await_submission <= done) {\n"
    "      // The marker covers everything needed: skip the driver fence wait and poll.\n"
    "      fences_awaited = size_t(std::min<uint64_t>(\n"
    "          fences_total, done > submission_completed_ ? done - submission_completed_ : 0));\n"
    "      fences_total = fences_awaited;\n"
    "      await_submission = 0;\n"
    "    }\n"
    "  }\n"), (
    # F6: retired fences go to a graveyard instead of being reset and reused
    "  fences_free_.insert(fences_free_.cend(), submissions_in_flight_fences_.cbegin(),\n"
    "                      submissions_in_flight_fences_awaited_end);\n",
    "  if (tt_marker::Ready(*this)) {  // tt-android F6\n"
    "    for (auto it = submissions_in_flight_fences_.cbegin();\n"
    "         it != submissions_in_flight_fences_awaited_end; ++it) {\n"
    "      tt_marker::graveyard.emplace_back(submission_completed_ + fences_awaited, *it);\n"
    "    }\n"
    "    while (!tt_marker::graveyard.empty() &&\n"
    "           tt_marker::graveyard.front().first + 8 <= submission_completed_ + fences_awaited) {\n"
    "      dfn.vkDestroyFence(device, tt_marker::graveyard.front().second, nullptr);\n"
    "      tt_marker::graveyard.pop_front();\n"
    "    }\n"
    "  } else {\n"
    "    fences_free_.insert(fences_free_.cend(), submissions_in_flight_fences_.cbegin(),\n"
    "                        submissions_in_flight_fences_awaited_end);\n"
    "  }\n"),
    prelude=MARKER, includes=("#include <chrono>", "#include <deque>", "#include <thread>"))
done.append("F5/F6 GPU progress from a memory marker (no slow fence queries)")

# ---- F7 The presenter's submission tracker asked the driver about every pending fence on
#      every swap; on KGSL each query on a not-yet-signalled fence takes ~13 ms. Fences
#      younger than 100 ms are treated as pending without asking. Blocking waits unchanged.
TRACKER_HELPERS = """// tt-android F7: fence age gate
namespace {
std::mutex tt_fence_mutex;
std::unordered_map<VkFence, std::chrono::steady_clock::time_point> tt_fence_submitted;
void TtStampFence(VkFence fence) {
  std::lock_guard<std::mutex> lock(tt_fence_mutex);
  tt_fence_submitted[fence] = std::chrono::steady_clock::now();
}
bool TtFenceTooYoung(VkFence fence) {
  std::lock_guard<std::mutex> lock(tt_fence_mutex);
  auto it = tt_fence_submitted.find(fence);
  return it != tt_fence_submitted.end() &&
         std::chrono::steady_clock::now() - it->second < std::chrono::milliseconds(100);
}
}  // namespace
"""
edit("src/ui/vulkan/vulkan_submission_tracker.cpp", (
    "namespace rex {\nnamespace ui {\nnamespace vulkan {\n",
    "namespace rex {\nnamespace ui {\nnamespace vulkan {\n" + TRACKER_HELPERS), (
    "      submission_tracker_->fences_pending_.emplace_back(submission_tracker_->submission_current_,\n"
    "                                                        fence_);\n",
    "      submission_tracker_->fences_pending_.emplace_back(submission_tracker_->submission_current_,\n"
    "                                                        fence_);\n"
    "      TtStampFence(fence_);  // tt-android F7\n"), (
    "      if (dfn.vkGetFenceStatus(device, pending_pair.second) != VK_SUCCESS) {\n        break;\n      }\n",
    "      if (TtFenceTooYoung(pending_pair.second) ||  // tt-android F7\n"
    "          dfn.vkGetFenceStatus(device, pending_pair.second) != VK_SUCCESS) {\n        break;\n      }\n"), (
    "      if (dfn.vkGetFenceStatus(device, pending_pair.second) == VK_SUCCESS) {\n        break;\n      }\n",
    "      if (!TtFenceTooYoung(pending_pair.second) &&  // tt-android F7\n"
    "          dfn.vkGetFenceStatus(device, pending_pair.second) == VK_SUCCESS) {\n        break;\n      }\n"),
    includes=("#include <chrono>", "#include <mutex>", "#include <unordered_map>"))
done.append("F7 submission tracker skips status queries on young fences")

# ---- F8 Presenter. On Android the swapchain acquire only completes at the display's
#      refresh, so the GPU queue must not wait for it: the UI thread waits for the acquire
#      on a CPU fence and the paint is submitted with no wait semaphore. The paint signals
#      the present semaphore and the present waits for it GPU-side. (Until v80 the UI thread
#      also waited for its paint on the CPU; the paint sits behind the game's queued GPU
#      work, so the screen was repainted only ~22 times/s while the game made ~44 frames.)
edit(PRESENTER, (
    """  VkSemaphore acquire_semaphore = paint_submission.acquire_semaphore();
  uint32_t swapchain_image_index;
  VkResult acquire_result =
      dfn.vkAcquireNextImageKHR(device, paint_context_.swapchain, UINT64_MAX, acquire_semaphore,
                                VK_NULL_HANDLE, &swapchain_image_index);
""",
    """  uint32_t swapchain_image_index;
  // tt-android F8: acquire with a CPU fence (one per painting thread) instead of a
  // semaphore the GPU queue would have to wait on.
  thread_local VkFence tt_acquire_fence = VK_NULL_HANDLE;
  if (tt_acquire_fence == VK_NULL_HANDLE) {
    VkFenceCreateInfo fence_info = {};
    fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    if (dfn.vkCreateFence(device, &fence_info, nullptr, &tt_acquire_fence) != VK_SUCCESS) {
      tt_acquire_fence = VK_NULL_HANDLE;
    }
  }
  VkResult acquire_result = dfn.vkAcquireNextImageKHR(
      device, paint_context_.swapchain, UINT64_MAX, VK_NULL_HANDLE, tt_acquire_fence,
      &swapchain_image_index);
  if (acquire_result == VK_SUCCESS || acquire_result == VK_SUBOPTIMAL_KHR) {
    dfn.vkWaitForFences(device, 1, &tt_acquire_fence, VK_TRUE, UINT64_MAX);
    dfn.vkResetFences(device, 1, &tt_acquire_fence);
  }
"""), (
    """  submit_info.waitSemaphoreCount = 1;
  submit_info.pWaitSemaphores = &acquire_semaphore;
  submit_info.pWaitDstStageMask = &acquire_semaphore_wait_stage;
""",
    """  submit_info.waitSemaphoreCount = 0;  // tt-android F8: acquired on the CPU
  submit_info.pWaitSemaphores = nullptr;
  submit_info.pWaitDstStageMask = nullptr;
"""), (
    # the acquire semaphore's wait stage is no longer used
    "  VkPipelineStageFlags acquire_semaphore_wait_stage = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;\n", ""))
done.append("F8 presenter: acquire waited on the CPU, paint not awaited by the UI thread")

# ---- F9 Every frame the emulator re-converted ~60 textures (~43 MB) on the GPU because the
#      game wrote somewhere in their memory pages. When a texture was invalidated only by
#      CPU writes, hash its guest bytes and skip the re-conversion if they're unchanged.
#      Textures written by the GPU (resolves) always reload. v55: ~27 of ~59 skipped.
edit("include/rex/graphics/shared_memory.h", (
    "class SharedMemory {\n public:\n",
    "class SharedMemory {\n public:\n"
    "  // tt-android F9: guest memory, for the texture content hash\n"
    "  memory::Memory& tt_guest_memory() const { return memory_; }\n"))
edit("src/graphics/pipeline/texture/cache.cpp", (
    "  Texture& texture = *static_cast<Texture*>(context);\n"
    "  texture.WatchCallback(global_lock, argument != 0);\n",
    "  Texture& texture = *static_cast<Texture*>(context);\n"
    "  if (invalidated_by_gpu) {  // tt-android F9: GPU-written textures must reload\n"
    "    std::lock_guard<std::mutex> lock(tt_tex_gpu_mutex);\n"
    "    tt_tex_gpu_dirty.insert(context);\n"
    "  }\n"
    "  texture.WatchCallback(global_lock, argument != 0);\n"),
    prelude="// tt-android F9: textures last invalidated by GPU writes\n"
            "std::mutex tt_tex_gpu_mutex;\nstd::unordered_set<const void*> tt_tex_gpu_dirty;\n",
    includes=("#include <mutex>", "#include <unordered_set>"))
edit("src/graphics/vulkan/texture_cache.cpp", (
    "  VulkanTexture& vulkan_texture = static_cast<VulkanTexture&>(texture);\n"
    "  TextureKey texture_key = vulkan_texture.key();\n",
    """  {  // tt-android F9: skip re-converting textures whose guest bytes didn't change
    struct State { TextureKey key; uint64_t base = 0, mips = 0; bool has_base = false, has_mips = false;
                   uint32_t streak = 0, since_check = 0; };
    static std::unordered_map<const void*, State> states;
    __atomic_fetch_add(&tt_tex_stats[0], 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&tt_work_stats[2], 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&tt_work_stats[3], uint64_t(texture.GetGuestBaseSize()) +
                       (load_mips ? uint64_t(texture.GetGuestMipsSize()) : 0), __ATOMIC_RELAXED);
    bool gpu_written;
    {
      std::lock_guard<std::mutex> lock(tt_tex_gpu_mutex);
      gpu_written = tt_tex_gpu_dirty.erase(&texture) != 0;
    }
    if (gpu_written) __atomic_fetch_add(&tt_tex_stats[2], 1, __ATOMIC_RELAXED);
    memory::Memory& mem = shared_memory().tt_guest_memory();
    const TextureKey key = texture.key();
    // Don't hash what will reload anyway (v78: 42 -> 3.5 MB hashed per frame, GPU thread
    // ~64% -> ~47% of a core): GPU-written textures always reload, and a CPU-written one
    // whose bytes changed on its last 8 loads is reloaded without hashing, re-checked every
    // 16th load.
    auto it = states.find(&texture);
    const bool known = it != states.end() && std::memcmp(&it->second.key, &key, sizeof(key)) == 0;
    const bool had_hash = known && (it->second.has_base || it->second.has_mips);
    const bool streaky = known && !gpu_written && it->second.streak >= 8 &&
                         (++it->second.since_check & 15) != 0;
    const bool skip_hash = gpu_written || streaky;
    uint64_t hash_base = 0, hash_mips = 0;
    if (!skip_hash && load_base && texture.GetGuestBaseSize()) {
      hash_base = XXH3_64bits(mem.TranslatePhysical(uint32_t(key.base_page) << 12),
                              texture.GetGuestBaseSize());
      __atomic_fetch_add(&tt_tex_stats[3], uint64_t(texture.GetGuestBaseSize()), __ATOMIC_RELAXED);
    }
    if (!skip_hash && load_mips && texture.GetGuestMipsSize()) {
      hash_mips = XXH3_64bits(mem.TranslatePhysical(uint32_t(key.mip_page) << 12),
                              texture.GetGuestMipsSize());
      __atomic_fetch_add(&tt_tex_stats[3], uint64_t(texture.GetGuestMipsSize()), __ATOMIC_RELAXED);
    }
    if (!gpu_written && !skip_hash && known &&
        (!load_base || (it->second.has_base && it->second.base == hash_base)) &&
        (!load_mips || (it->second.has_mips && it->second.mips == hash_mips))) {
      __atomic_fetch_add(&tt_tex_stats[1], 1, __ATOMIC_RELAXED);
      it->second.streak = 0;
      return true;  // same bytes as the last load: the GPU copy is still right
    }
    State& state = states[&texture];
    if (!known) state = State{};
    if (!gpu_written && !skip_hash && had_hash) ++state.streak;  // hashed and found changed
    state.key = key;
    // A streaky skip keeps the last hash, so the 16th-load re-check compares against it.
    if (load_base && !streaky) { state.base = hash_base; state.has_base = !gpu_written && !skip_hash; }
    if (load_mips && !streaky) { state.mips = hash_mips; state.has_mips = !gpu_written && !skip_hash; }
  }
  VulkanTexture& vulkan_texture = static_cast<VulkanTexture&>(texture);
  TextureKey texture_key = vulkan_texture.key();
"""),
    prelude="// tt-android F9 / D2 / D3\n"
            "extern std::mutex tt_tex_gpu_mutex;\n"
            "extern std::unordered_set<const void*> tt_tex_gpu_dirty;\n"
            "extern \"C\" uint64_t tt_work_stats[16];\n"
            "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_tex_stats[4] = {};\n",
    includes=("#include <cstdlib>", "#include <cstring>", "#include <mutex>", "#include <unordered_map>",
              "#include <unordered_set>", "#include <rex/hash.h>"))
done.append("F9 unchanged textures aren't re-converted")

# ---- F10 Two depth-only passes the game draws every frame into depth buffers 640 and 1120
#      pixels wide (~37 + ~57 draws) have no visible effect in rallies (v58-v60 screenshots
#      matched; the real table/player shadows are drawn elsewhere). Skipping them: ~+2 FPS
#      (40.6 -> 42.8). F11: a full-size pass (~96 draws per frame) that writes neither colour
#      nor depth, also no visible change (v63/v66): ~+0.5 FPS. Both counted in tt_work_stats[8].
#      Both are "visual-risk" changes (tested in one venue / character / mode): the launcher's
#      "Hidden passes" row turns them off (TT_DRAW_ALL_PASSES=1) for comparison.
edit(CP, (
    "  reg::RB_DEPTHCONTROL normalized_depth_control = draw_util::GetNormalizedDepthControl(regs);\n",
    "  reg::RB_DEPTHCONTROL normalized_depth_control = draw_util::GetNormalizedDepthControl(regs);\n"
    "  static const bool tt_skip_passes = std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr;\n"
    "  if (tt_skip_passes && normalized_depth_control.z_enable && normalized_depth_control.z_write_enable &&\n"
    "      !(pixel_shader ? draw_util::GetNormalizedColorMask(regs, pixel_shader->writes_color_targets())\n"
    "                     : 0)) {  // tt-android F10: unused depth-only passes\n"
    "    const uint32_t pitch = regs.Get<reg::RB_SURFACE_INFO>().surface_pitch;\n"
    "    if (pitch == 640 || pitch == 1120) {\n"
    "      __atomic_fetch_add(&tt_work_stats[8], 1, __ATOMIC_RELAXED);\n"
    "      return true;\n"
    "    }\n"
    "  }\n"
    "  if (tt_skip_passes && regs.Get<reg::RB_SURFACE_INFO>().surface_pitch == 1280 &&\n"
    "      !(normalized_depth_control.z_enable && normalized_depth_control.z_write_enable) &&\n"
    "      !(pixel_shader ? draw_util::GetNormalizedColorMask(regs, pixel_shader->writes_color_targets())\n"
    "                     : 0)) {  // tt-android F11: full-size pass that writes neither colour nor depth\n"
    "    __atomic_fetch_add(&tt_work_stats[8], 1, __ATOMIC_RELAXED);\n"
    "    return true;\n"
    "  }\n"),
    includes=("#include <cstdlib>",))
done.append("F10/F11 passes with no visible effect skipped (TT_DRAW_ALL_PASSES=1 draws them)")

# ---- F12 Skip render-target copies. When the game switches between its full, half and
#      quarter-size passes, the emulator copies EDRAM contents between host render targets
#      (~26 draws, ~5 screens of pixels per frame). Skipping them measured +1.4 FPS twice
#      (v74, v76) with no visible change in two venues. Visual risk: listed in HANDOFF.md,
#      switched off with the other skips by TT_DRAW_ALL_PASSES=1.
edit("src/graphics/vulkan/render_target_cache.cpp", (
    "            command_buffer.CmdVkDraw(transfer_vertex_count, 1, 0, 0);\n",
    "            static const bool tt_skip_copies =  // tt-android F12\n"
    "                std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr;\n"
    "            if (!tt_skip_copies) command_buffer.CmdVkDraw(transfer_vertex_count, 1, 0, 0);\n"),
    includes=("#include <cstdlib>",))
done.append("F12 render-target copies skipped (TT_DRAW_ALL_PASSES=1 keeps them)")

# ---- F13 Stereo sound mix. Android reports its output as 6 channels, so the emulator handed
#      it the game's 5.1 mix and Android folded that down to the Thor's two speakers itself:
#      ball hits sounded muffled, hollow and distant. The SDK's own 5.1 -> stereo fold (the
#      path it uses for stereo devices) runs instead and Android gets plain stereo, with the
#      rear channels (mostly the arena's echo) at 0.25 instead of the SDK's 0.707.
#      v79: "way better"; v81: owner picked the 0.25 rear level ("much less echo").
edit("src/audio/sdl/sdl_audio_driver.cpp", (
    "  if (obtained_spec.channels <= 2) {\n",
    "#if defined(__ANDROID__)\n"
    "  {  // tt-android F13: the SDK's stereo fold, rear channels turned down\n"
    "    StereoFold fold = GetStereoFold();\n"
    "    fold.surround = 0.25f;\n"
    "    SetStereoFold(fold);\n"
    "    obtained_spec.channels = 2;\n"
    "  }\n"
    "#endif\n"
    "  if (obtained_spec.channels <= 2) {\n"))
done.append("F13 stereo sound mix by the SDK, less rear echo (not Android's 5.1 downmix)")

# ---- F14 Repaint only on new game frames. The overlay (an ImGui UI drawer) keeps the UI
#      thread repainting; it used to be capped at 60 Hz, but the game makes ~44 frames/s, so
#      many repaints re-presented the same image. The UI thread now waits here, BEFORE taking
#      the guest image, until the game has refreshed it (at most ~120 Hz; at least every
#      100 ms so menus stay alive). With F8 this gives one paint per game frame (v79: 43.6
#      new images, 43.6 repaints per second). D4 tt_present_stats: [0] guest refreshes
#      [1] screen paints.
edit("src/ui/presenter.cpp", (
    "  // Now, it's known that `ready == writable` on the host presentation side.\n",
    "  __atomic_fetch_add(&tt_present_stats[0], 1, __ATOMIC_RELEASE);  // tt-android F14\n"
    "  // Now, it's known that `ready == writable` on the host presentation side.\n"), (
    "void Presenter::WaitForUITickFromUIThread() {\n",
    "void Presenter::WaitForUITickFromUIThread() {\n"
    "  __atomic_fetch_add(&tt_present_stats[1], 1, __ATOMIC_RELAXED);  // tt-android F14\n"
    "  {\n"
    "    using clock = std::chrono::steady_clock;\n"
    "    static uint64_t last_seen = 0;\n"
    "    static auto last_paint = clock::now();\n"
    "    while (true) {\n"
    "      const auto now = clock::now();\n"
    "      const uint64_t cur = __atomic_load_n(&tt_present_stats[0], __ATOMIC_ACQUIRE);\n"
    "      if ((cur != last_seen && now >= last_paint + std::chrono::milliseconds(8)) ||\n"
    "          now >= last_paint + std::chrono::milliseconds(100)) {\n"
    "        last_seen = cur;\n"
    "        last_paint = now;\n"
    "        break;\n"
    "      }\n"
    "      std::this_thread::sleep_for(std::chrono::milliseconds(1));\n"
    "    }\n"
    "  }\n"),
    prelude='extern "C" __attribute__((visibility("default"))) uint64_t tt_present_stats[4] = {};  // F14 / D4\n',
    includes=("#include <chrono>", "#include <cstdint>", "#include <thread>"))
done.append("F14 screen repainted once per new game frame")

# ====================================================================== DIAGNOSTICS

# ---- D1 frame pacing: guest vblanks, command-processor WAIT_REG_MEM sleeps, swaps
edit("src/graphics/graphics_system.cpp", (
    "void GraphicsSystem::MarkVblank() {\n",
    "void GraphicsSystem::MarkVblank() {\n"
    "  __atomic_fetch_add(&tt_frame_stats[0], 1, __ATOMIC_RELAXED);  // tt-android D1\n"),
    prelude="// tt-android D1: [0] guest vblanks [1] CP wait-for-memory sleeps [2] their ns [3] swaps\n"
            "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_frame_stats[8] = {};\n",
    includes=("#include <cstdint>",))
edit("src/graphics/command_processor.cpp", (
    "          rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));\n",
    "          const auto tt_t0 = std::chrono::steady_clock::now();  // tt-android D1\n"
    "          rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));\n"
    "          __atomic_fetch_add(&tt_frame_stats[1], 1, __ATOMIC_RELAXED);\n"
    "          __atomic_fetch_add(&tt_frame_stats[2],\n"
    "                             uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
    "                                 std::chrono::steady_clock::now() - tt_t0).count()),\n"
    "                             __ATOMIC_RELAXED);\n"), (
    "  ++counter_;\n  return true;\n",
    "  ++counter_;\n"
    "  __atomic_fetch_add(&tt_frame_stats[3], 1, __ATOMIC_RELAXED);  // tt-android D1\n"
    "  return true;\n"),
    prelude="extern \"C\" uint64_t tt_frame_stats[8];  // tt-android D1\n",
    includes=("#include <chrono>", "#include <cstdint>"))
done.append("D1 frame pacing counters")

# ---- D2 GPU work per frame: draws, resolves, uploads (texture loads are counted in F9)
edit(CP, (
    "bool VulkanCommandProcessor::IssueDraw(xenos::PrimitiveType prim_type, uint32_t index_count,\n"
    "                                       IndexBufferInfo* index_buffer_info,\n"
    "                                       bool major_mode_explicit) {\n",
    "bool VulkanCommandProcessor::IssueDraw(xenos::PrimitiveType prim_type, uint32_t index_count,\n"
    "                                       IndexBufferInfo* index_buffer_info,\n"
    "                                       bool major_mode_explicit) {\n"
    "  __atomic_fetch_add(&tt_work_stats[0], 1, __ATOMIC_RELAXED);  // tt-android D2\n"), (
    "bool VulkanCommandProcessor::IssueCopy() {\n",
    "bool VulkanCommandProcessor::IssueCopy() {\n"
    "  __atomic_fetch_add(&tt_work_stats[1], 1, __ATOMIC_RELAXED);  // tt-android D2\n"),
    prelude="// tt-android D2: [0] draws [1] resolves [2] texture loads [3] texture bytes\n"
            "// [6] upload calls [7] upload bytes\n"
            "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_work_stats[16] = {};\n")
edit("src/graphics/vulkan/shared_memory.cpp", (
    "    const std::vector<std::pair<uint32_t, uint32_t>>& upload_page_ranges) {\n",
    "    const std::vector<std::pair<uint32_t, uint32_t>>& upload_page_ranges) {\n"
    "  __atomic_fetch_add(&tt_work_stats[6], 1, __ATOMIC_RELAXED);  // tt-android D2\n"
    "  for (const auto& range : upload_page_ranges) {\n"
    "    __atomic_fetch_add(&tt_work_stats[7], uint64_t(range.second) << page_size_log2(),\n"
    "                       __ATOMIC_RELAXED);\n"
    "  }\n"),
    prelude="extern \"C\" uint64_t tt_work_stats[16];  // tt-android D2\n",
    includes=("#include <cstdint>",))
done.append("D2/D3 GPU work and texture counters")


# ---- F15 Uploads before the pass (v84; the NFS Most Wanted Android port found the same).
#      Each time a draw needs guest memory that the game wrote
#      since its last use (~310 times per frame), the emulator ends the open render pass,
#      copies the data into its big GPU buffer and starts the pass again. On a phone GPU a
#      pass restart is expensive. With "hoist", the copy is recorded BEFORE the open pass
#      instead (the command list is replayed later, so this is only a matter of order), as
#      long as no draw recorded so far in that pass read those pages.
edit("include/rex/graphics/vulkan/deferred_command_buffer.h", (
    "  void Execute(VkCommandBuffer command_buffer);\n",
    "  void Execute(VkCommandBuffer command_buffer);\n"
    "\n"
    "  // tt-android F15: copies to run just before the command at `position` (a pass begin).\n"
    "  size_t TtSize() const { return command_stream_.size(); }\n"
    "  void TtHoistCopy(size_t position, VkBuffer src, VkBuffer dst, const VkBufferCopy& region,\n"
    "                   VkPipelineStageFlags read_stages, VkAccessFlags read_access) {\n"
    "    tt_hoists_.push_back({position, src, dst, region});\n"
    "    tt_hoist_read_stages_ = read_stages;\n"
    "    tt_hoist_read_access_ = read_access;\n"
    "  }\n"
    "  struct TtHoist {\n"
    "    size_t position;\n"
    "    VkBuffer src;\n"
    "    VkBuffer dst;\n"
    "    VkBufferCopy region;\n"
    "  };\n"
    "  std::vector<TtHoist> tt_hoists_;\n"
    "  VkPipelineStageFlags tt_hoist_read_stages_ = 0;\n"
    "  VkAccessFlags tt_hoist_read_access_ = 0;\n"))
edit("src/graphics/vulkan/deferred_command_buffer.cpp", (
    "void DeferredCommandBuffer::Reset() {\n  command_stream_.clear();\n",
    "void DeferredCommandBuffer::Reset() {\n  command_stream_.clear();\n  tt_hoists_.clear();\n"), (
    "  while (stream_remaining) {\n"
    "    const CommandHeader& header = *reinterpret_cast<const CommandHeader*>(stream);\n",
    "  size_t tt_hoist_next = 0;\n"
    "  while (stream_remaining) {\n"
    "    // tt-android F15: uploads that were moved in front of the pass that begins here.\n"
    "    if (tt_hoist_next < tt_hoists_.size() &&\n"
    "        tt_hoists_[tt_hoist_next].position == size_t(stream - command_stream_.data())) {\n"
    "      const size_t position = tt_hoists_[tt_hoist_next].position;\n"
    "      VkBufferMemoryBarrier barrier = {};\n"
    "      barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER;\n"
    "      barrier.srcAccessMask = tt_hoist_read_access_;\n"
    "      barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
    "      barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;\n"
    "      barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;\n"
    "      barrier.buffer = tt_hoists_[tt_hoist_next].dst;\n"
    "      barrier.offset = 0;\n"
    "      barrier.size = VK_WHOLE_SIZE;\n"
    "      dfn.vkCmdPipelineBarrier(command_buffer, tt_hoist_read_stages_,\n"
    "                               VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 1, &barrier, 0,\n"
    "                               nullptr);\n"
    "      while (tt_hoist_next < tt_hoists_.size() &&\n"
    "             tt_hoists_[tt_hoist_next].position == position) {\n"
    "        const TtHoist& hoist = tt_hoists_[tt_hoist_next++];\n"
    "        dfn.vkCmdCopyBuffer(command_buffer, hoist.src, hoist.dst, 1, &hoist.region);\n"
    "      }\n"
    "      barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
    "      barrier.dstAccessMask = tt_hoist_read_access_;\n"
    "      dfn.vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,\n"
    "                               tt_hoist_read_stages_, 0, 0, nullptr, 1, &barrier, 0, nullptr);\n"
    "    }\n"
    "    const CommandHeader& header = *reinterpret_cast<const CommandHeader*>(stream);\n"))

# The pass a draw opens is where uploads may be moved to; passes opened for render-target
# copies are not. Both are counted.
edit("include/rex/graphics/vulkan/command_processor.h", (
    "  bool in_render_pass_ = false;\n",
    "  bool in_render_pass_ = false;\n"
    "  size_t tt_hoist_position_ = SIZE_MAX;  // tt-android F15\n"
    " public:\n"
    "  size_t TtHoistPosition() const { return in_render_pass_ ? tt_hoist_position_ : SIZE_MAX; }\n"
    " private:\n"))
edit(CP, (
    "  __atomic_fetch_add(&tt_work_stats[0], 1, __ATOMIC_RELAXED);  // tt-android D2\n",
    "  __atomic_fetch_add(&tt_work_stats[0], 1, __ATOMIC_RELAXED);  // tt-android D2\n"
    "  if (shared_memory_) shared_memory_->TtBeginDraw();  // tt-android F15\n"), (
    "  current_framebuffer_ = framebuffer;\n\n  if (use_dynamic_rendering) {\n"
    "    VkRenderingAttachmentInfo color_attachments[xenos::kMaxColorRenderTargets];\n",
    "  current_framebuffer_ = framebuffer;\n"
    "  tt_hoist_position_ = deferred_command_buffer_.TtSize();  // tt-android F15\n"
    "  if (shared_memory_) shared_memory_->TtBeginPass();\n"
    "  __atomic_fetch_add(&tt_work_stats[9], 1, __ATOMIC_RELAXED);\n"
    "\n  if (use_dynamic_rendering) {\n"
    "    VkRenderingAttachmentInfo color_attachments[xenos::kMaxColorRenderTargets];\n"), (
    "  current_framebuffer_ = framebuffer;\n\n  if (use_dynamic_rendering) {\n"
    "    VkRenderingAttachmentInfo color_attachment = {};\n",
    "  current_framebuffer_ = framebuffer;\n"
    "  tt_hoist_position_ = SIZE_MAX;  // tt-android F15\n"
    "  __atomic_fetch_add(&tt_work_stats[9], 1, __ATOMIC_RELAXED);\n"
    "\n  if (use_dynamic_rendering) {\n"
    "    VkRenderingAttachmentInfo color_attachment = {};\n"), (
    # Draws that keep a vertex buffer bound without asking for it again still read it.
    "      if (vertex_buffers_in_sync_[vfetch_index >> 6] & vfetch_bit) {\n"
    "        continue;\n",
    "      if (vertex_buffers_in_sync_[vfetch_index >> 6] & vfetch_bit) {\n"
    "        shared_memory_->TtMarkUsed(vertex_buffer_states_[vfetch_index].address << 2,\n"
    "                                   vertex_buffer_states_[vfetch_index].size << 2);\n"
    "        continue;\n"), (
    "      if (state.address == vfetch_constant.address && state.size == vfetch_constant.size) {\n"
    "        vertex_buffers_in_sync_[vfetch_index >> 6] |= vfetch_bit;\n"
    "        continue;\n",
    "      if (state.address == vfetch_constant.address && state.size == vfetch_constant.size) {\n"
    "        vertex_buffers_in_sync_[vfetch_index >> 6] |= vfetch_bit;\n"
    "        shared_memory_->TtMarkUsed(state.address << 2, state.size << 2);\n"
    "        continue;\n"))
edit("include/rex/graphics/shared_memory.h", (
    "  bool RequestRange(uint32_t start, uint32_t length);\n",
    "  bool RequestRange(uint32_t start, uint32_t length);\n"
    "\n"
    "  // tt-android F15: which pages draws have read since the open render pass began. A draw\n"
    "  // asks for its memory before it opens its pass, so what the current draw asked for is\n"
    "  // kept in a list and stamped again when a new pass begins.\n"
    "  void TtBeginDraw() { tt_draw_ranges_.clear(); }\n"
    "  void TtBeginPass() {\n"
    "    ++tt_pass_generation_;\n"
    "    for (const std::pair<uint32_t, uint32_t>& range : tt_draw_ranges_) {\n"
    "      TtStamp(range.first, range.second);\n"
    "    }\n"
    "  }\n"
    "  void TtMarkUsed(uint32_t start, uint32_t length) {\n"
    "    if (!length || start >= kBufferSize) return;\n"
    "    length = std::min(length, kBufferSize - start);\n"
    "    tt_draw_ranges_.emplace_back(start, length);\n"
    "    TtStamp(start, length);\n"
    "  }\n"
    "  void TtStamp(uint32_t start, uint32_t length) {\n"
    "    if (tt_page_generation_.empty()) tt_page_generation_.resize(kBufferSize >> page_size_log2_);\n"
    "    const uint32_t last = (start + (length - 1)) >> page_size_log2_;\n"
    "    for (uint32_t i = start >> page_size_log2_; i <= last; ++i) {\n"
    "      tt_page_generation_[i] = tt_pass_generation_;\n"
    "    }\n"
    "  }\n"
    "  bool TtUsedInPass(uint32_t page_first, uint32_t page_count) const {\n"
    "    for (uint32_t i = page_first; i < page_first + page_count; ++i) {\n"
    "      if (i < tt_page_generation_.size() && tt_page_generation_[i] == tt_pass_generation_) {\n"
    "        return true;\n"
    "      }\n"
    "    }\n"
    "    return false;\n"
    "  }\n"
    "  std::vector<uint32_t> tt_page_generation_;\n"
    "  std::vector<std::pair<uint32_t, uint32_t>> tt_draw_ranges_;\n"
    "  uint32_t tt_pass_generation_ = 1;\n"),
    includes=("#include <algorithm>",))
edit("src/graphics/shared_memory.cpp", (
    "  if (upload_ranges_.empty()) {\n    return true;\n  }\n\n  return UploadRanges(upload_ranges_);\n",
    "  // tt-android F15: what was asked for is read by the draw that follows, so it counts as\n"
    "  // read in this pass from now on (marked after its own upload, which may still move).\n"
    "  const bool tt_uploaded = upload_ranges_.empty() || UploadRanges(upload_ranges_);\n"
    "  for (const std::pair<uint32_t, uint32_t>& range : merged_ranges) {\n"
    "    TtMarkUsed(range.first, range.second);\n"
    "  }\n"
    "  return tt_uploaded;\n"))
edit("src/graphics/vulkan/shared_memory.cpp", (
    "  // upload_page_ranges are sorted, use them to determine the range for the\n"
    "  // ordering barrier.\n"
    "  Use(Usage::kTransferDestination,\n"
    "      std::make_pair(upload_page_ranges.front().first << page_size_log2(),\n"
    "                     (upload_page_ranges.back().first + upload_page_ranges.back().second -\n"
    "                      upload_page_ranges.front().first)\n"
    "                         << page_size_log2()));\n"
    "  command_processor_.SubmitBarriers(true);\n"
    "  DeferredCommandBuffer& command_buffer = command_processor_.deferred_command_buffer();\n",
    "  // tt-android F15: inside a pass opened by a draw, an upload of pages that no draw of\n"
    "  // this pass has read is recorded in front of the pass instead of cutting it.\n"
    "  DeferredCommandBuffer& command_buffer = command_processor_.deferred_command_buffer();\n"
    "  static const bool tt_hoist_enabled = std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr;\n"
    "  size_t tt_hoist_position = SIZE_MAX;\n"
    "  VkPipelineStageFlags tt_read_stages;\n"
    "  VkAccessFlags tt_read_access;\n"
    "  GetUsageMasks(Usage::kRead, tt_read_stages, tt_read_access);\n"
    "  if (command_processor_.TtHoistPosition() != SIZE_MAX) {\n"
    "    bool movable = last_usage_ == Usage::kRead && !last_written_range_.second;\n"
    "    for (size_t i = 0; movable && i < upload_page_ranges.size(); ++i) {\n"
    "      movable = !TtUsedInPass(upload_page_ranges[i].first, upload_page_ranges[i].second);\n"
    "    }\n"
    "    __atomic_fetch_add(&tt_work_stats[movable ? 10 : 11], 1, __ATOMIC_RELAXED);\n"
    "    if (movable && tt_hoist_enabled) tt_hoist_position = command_processor_.TtHoistPosition();\n"
    "  }\n"
    "  if (tt_hoist_position == SIZE_MAX) {\n"
    "    // upload_page_ranges are sorted, use them to determine the range for the\n"
    "    // ordering barrier.\n"
    "    Use(Usage::kTransferDestination,\n"
    "        std::make_pair(upload_page_ranges.front().first << page_size_log2(),\n"
    "                       (upload_page_ranges.back().first + upload_page_ranges.back().second -\n"
    "                        upload_page_ranges.front().first)\n"
    "                           << page_size_log2()));\n"
    "    command_processor_.SubmitBarriers(true);\n"
    "  }\n"
    "  auto tt_copy = [&](VkBuffer upload_buffer) {\n"
    "    if (tt_hoist_position == SIZE_MAX) {\n"
    "      command_buffer.CmdVkCopyBuffer(upload_buffer, buffer_, uint32_t(upload_regions_.size()),\n"
    "                                     upload_regions_.data());\n"
    "      return;\n"
    "    }\n"
    "    for (const VkBufferCopy& region : upload_regions_) {\n"
    "      command_buffer.TtHoistCopy(tt_hoist_position, upload_buffer, buffer_, region,\n"
    "                                 tt_read_stages, tt_read_access);\n"
    "    }\n"
    "  };\n"), (
    "        assert_true(upload_buffer_previous != VK_NULL_HANDLE);\n"
    "        command_buffer.CmdVkCopyBuffer(upload_buffer_previous, buffer_,\n"
    "                                       uint32_t(upload_regions_.size()), upload_regions_.data());\n",
    "        assert_true(upload_buffer_previous != VK_NULL_HANDLE);\n"
    "        tt_copy(upload_buffer_previous);\n"), (
    "    assert_true(upload_buffer_previous != VK_NULL_HANDLE);\n"
    "    command_buffer.CmdVkCopyBuffer(upload_buffer_previous, buffer_,\n"
    "                                   uint32_t(upload_regions_.size()), upload_regions_.data());\n",
    "    assert_true(upload_buffer_previous != VK_NULL_HANDLE);\n"
    "    tt_copy(upload_buffer_previous);\n"),
    includes=("#include <cstdlib>",))

done.append("F15 uploads recorded in front of the open render pass")


# ============================================================ TESTS (v87, temporary)
# TT_TEST=skipres: F10 skips every draw into the 640- and 1120-wide depth buffers, but the game
# still resolves those (now empty) buffers into textures every frame: 1120x704 and 640x480
# depth, ~10 MB of the ~44 MB resolved per frame, each resolve being three passes over the
# pixels. With skipres each such destination is resolved its first 4 times only.
edit("src/graphics/vulkan/render_target_cache.cpp", (
    "  // Nothing to copy/clear.\n"
    "  if (!resolve_info.coordinate_info.width_div_8 || !resolve_info.height_div_8) {\n"
    "    return true;\n"
    "  }\n",
    "  // Nothing to copy/clear.\n"
    "  if (!resolve_info.coordinate_info.width_div_8 || !resolve_info.height_div_8) {\n"
    "    return true;\n"
    "  }\n"
    "  if (resolve_info.copy_dest_extent_length && resolve_info.IsCopyingDepth() &&\n"
    "      (resolve_info.depth_edram_info.pitch_tiles == 8 ||\n"
    "       resolve_info.depth_edram_info.pitch_tiles == 14)) {  // tt-android test (v87)\n"
    "    static const char* const tt_test = std::getenv(\"TT_TEST\");\n"
    "    static const bool tt_skip = std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr && tt_test &&\n"
    "                                !std::strcmp(tt_test, \"skipres\");\n"
    "    static std::unordered_map<uint32_t, uint32_t> tt_resolves;\n"
    "    if (tt_skip && tt_resolves[resolve_info.copy_dest_base]++ >= 4) {\n"
    "      __atomic_fetch_add(&tt_work_stats[13], 1, __ATOMIC_RELAXED);\n"
    "      resolve_info.copy_dest_extent_length = 0;  // clears still happen\n"
    "    }\n"
    "  }\n"),
    prelude="extern \"C\" uint64_t tt_work_stats[16];\n",
    includes=("#include <cstdlib>", "#include <cstring>", "#include <unordered_map>"))
done.append("TEST (v87) skipres")

print("\n".join(done))
