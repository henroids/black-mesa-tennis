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
  F15       uploads recorded in front of the open render pass instead of ending and
            restarting it (render passes 256 -> 68 per frame)                    ~46->52 FPS
  F16       resolves of the two depth buffers F10 leaves empty: done 4 times per
            destination, then skipped                                            ~52->55 FPS
  F17       plain colour resolves become one image copy render target -> host texture
            (4x sources averaged by vkCmdResolveImage); the texture is sampled through
            a red/blue-swapped view; guest memory at the destination is not written
                                                     ~55->60 FPS, then GPU 96 -> 86 % at 60
  F18       plain depth resolves written straight into the host texture by a small
            compute shader (SPIR-V embedded below)                     GPU 83.5 -> 81 % at 60
  F19       persistent VkPipelineCache next to the shader storage (Qualcomm's driver
            otherwise recompiles every pipeline on every launch: Retroid Pocket Duo)
  (F10-F12 and F15-F18 are all switched off by TT_DRAW_ALL_PASSES=1: launcher
   "Hidden passes -> Drawn")
  Measured on the AYN Thor, Turnip Gen8 V38/V39, 720p, FXAA, 60 FPS setting, rallies.

DIAGNOSTICS (counters read by tt_profiler.cpp via dlsym; cheap atomic adds)
  D1  tt_frame_stats  [0] guest vblanks [1] CP wait-for-memory sleeps [2] their ns [3] swaps
  D2  tt_work_stats   [0] draws [1] resolves [2] texture loads [3] texture bytes
                      [6] upload calls [7] upload bytes [8] draws skipped by F10/F11
                      [9] render passes begun [10] uploads moved by F15 [11] uploads that
                      still ended a pass [12] F18 depth resolves [13] F16 resolves skipped
                      [14] F17 colour resolves copied [15] colour resolves
  D3  tt_tex_stats    [0] texture loads asked [1] skipped (unchanged) [2] GPU-written
                      [3] bytes hashed
  D4  tt_present_stats [0] new game images [1] screen paints (F14)
  D5  "tt-slow: <step> took N ms" log lines for pipeline creation, shader translation,
      controller rumble, texture requests, memory uploads and whole draws of 15 ms or more
      (Retroid Pocket Duo first-serve stutter). A whole draw includes the other steps.
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


# ---- F16 (v87: 51.9 -> 55.5 FPS, ~7 MB less resolved per frame). F10 skips every draw into the 640- and 1120-wide depth buffers, but the game
# still resolves those (now empty) buffers into textures every frame: 1120x704 and 640x480
# depth, ~10 MB of the ~44 MB resolved per frame, each resolve being three passes over the
# pixels. With F16 each such destination is resolved its first 4 times only.
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
    "       resolve_info.depth_edram_info.pitch_tiles == 14)) {  // tt-android F16\n"
    "    static const bool tt_skip = std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr;\n"
    "    static std::unordered_map<uint32_t, uint32_t> tt_resolves;\n"
    "    if (tt_skip && tt_resolves[resolve_info.copy_dest_base]++ >= 4) {\n"
    "      __atomic_fetch_add(&tt_work_stats[13], 1, __ATOMIC_RELAXED);\n"
    "      resolve_info.copy_dest_extent_length = 0;  // clears still happen\n"
    "    }\n"
    "  }\n"),
    prelude="extern \"C\" uint64_t tt_work_stats[16];\n",
    includes=("#include <cstdlib>", "#include <cstring>", "#include <unordered_map>"))
done.append("F16 resolves of the depth buffers F10 leaves empty skipped")


# ---- F17 (v89: rallies ~55 -> 60 FPS capped, GPU ~90 % instead of 99 %): colour resolves copied straight from the host render target into the host
# texture (one image copy) instead of render target -> EDRAM buffer -> guest bytes in shared
# memory -> scratch buffer -> texture. Only when everything is plain: 8888 colour, no MSAA,
# no offsets, one render target owns the whole area, and exactly one existing 8888 texture
# sits at the destination address. Guest memory is NOT written in that case.
# The resolve swaps red and blue on its way to guest memory; the raw copy doesn't, so a
# texture filled this way is sampled through a view with red and blue swapped.
# tt_work_stats[14] counts direct copies.
edit("include/rex/graphics/vulkan/deferred_command_buffer.h", (
    "  void Execute(VkCommandBuffer command_buffer);\n",
    "  void Execute(VkCommandBuffer command_buffer);\n"
    "\n"
    "  // tt-android F17: one image-to-image copy, run in order with everything else.\n"
    "  struct TtArgsCopyImage {\n"
    "    VkImage src;\n"
    "    VkImage dst;\n"
    "    VkImageCopy region;\n"
    "    bool resolve;  // average the source's samples\n"
    "  };\n"
    "  void TtCmdCopyImage(VkImage src, VkImage dst, const VkImageCopy& region,\n"
    "                      bool resolve = false) {\n"
    "    auto& args = *reinterpret_cast<TtArgsCopyImage*>(\n"
    "        WriteCommand(Command::kTtCopyImage, sizeof(TtArgsCopyImage)));\n"
    "    args.src = src;\n"
    "    args.dst = dst;\n"
    "    args.region = region;\n"
    "    args.resolve = resolve;\n"
    "  }\n"), (
    "    kVkCopyBufferToImage,\n",
    "    kVkCopyBufferToImage,\n    kTtCopyImage,\n"))
edit("src/graphics/vulkan/deferred_command_buffer.cpp", (
    "      case Command::kVkCopyBufferToImage: {\n",
    "      case Command::kTtCopyImage: {  // tt-android F17\n"
    "        auto& args = *reinterpret_cast<const TtArgsCopyImage*>(stream);\n"
    "        if (args.resolve) {\n"
    "          VkImageResolve resolve_region;\n"
    "          resolve_region.srcSubresource = args.region.srcSubresource;\n"
    "          resolve_region.srcOffset = args.region.srcOffset;\n"
    "          resolve_region.dstSubresource = args.region.dstSubresource;\n"
    "          resolve_region.dstOffset = args.region.dstOffset;\n"
    "          resolve_region.extent = args.region.extent;\n"
    "          dfn.vkCmdResolveImage(command_buffer, args.src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,\n"
    "                                args.dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1,\n"
    "                                &resolve_region);\n"
    "          break;\n"
    "        }\n"
    "        dfn.vkCmdCopyImage(command_buffer, args.src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,\n"
    "                           args.dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &args.region);\n"
    "      } break;\n"
    "\n"
    "      case Command::kVkCopyBufferToImage: {\n"))
edit("include/rex/ui/vulkan/functions/device_1_0.inc", (
    "XE_UI_VULKAN_FUNCTION(vkCmdCopyBufferToImage)\n",
    "XE_UI_VULKAN_FUNCTION(vkCmdCopyBufferToImage)\nXE_UI_VULKAN_FUNCTION(vkCmdCopyImage)\n"), (
    "XE_UI_VULKAN_FUNCTION(vkCmdPushConstants)\n",
    "XE_UI_VULKAN_FUNCTION(vkCmdPushConstants)\nXE_UI_VULKAN_FUNCTION(vkCmdResolveImage)\n"))
edit("include/rex/graphics/pipeline/texture/cache.h", (
    "  rex::thread::global_critical_region global_critical_region_;\n",
    " protected:  // tt-android F17\n"
    "  rex::thread::global_critical_region global_critical_region_;\n"
    " private:\n"), (
    "  std::unordered_map<TextureKey, std::unique_ptr<Texture>, TextureKey::Hasher> textures_;\n",
    " protected:  // tt-android F17\n"
    "  std::unordered_map<TextureKey, std::unique_ptr<Texture>, TextureKey::Hasher> textures_;\n"
    " private:\n"))
edit("include/rex/graphics/vulkan/texture_cache.h", (
    "    VkImage image() const { return image_; }\n",
    "    VkImage image() const { return image_; }\n"
    "    // tt-android F17: filled by a raw copy from a render target (red/blue not swapped).\n"
    "    bool tt_rb_swapped_ = false;\n"), (
    "  void GetTextureUsageMasks(VulkanTexture::Usage usage, VkPipelineStageFlags& stage_mask,\n",
    " public:\n"
    "  // tt-android F17: the host image a colour resolve to this page can be copied into\n"
    "  // directly (made ready as a transfer destination), or null.\n"
    "  VkImage TtDirectResolveTarget(uint32_t base_page, uint32_t width, uint32_t height,\n"
    "                                uint32_t endian, bool rb_swapped, VkExtent2D& extent_out,\n"
    "                                xenos::TextureFormat format = xenos::TextureFormat::k_8_8_8_8);\n"
    " protected:\n"
    "  void GetTextureUsageMasks(VulkanTexture::Usage usage, VkPipelineStageFlags& stage_mask,\n"))
edit("src/graphics/vulkan/texture_cache.cpp", (
    "  image_create_info.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;\n"
    "  image_create_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;\n"
    "  image_create_info.queueFamilyIndexCount = 0;\n"
    "  image_create_info.pQueueFamilyIndices = nullptr;\n"
    "  image_create_info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;\n"
    "  VkImageFormatListCreateInfo image_format_list_create_info;\n",
    "  image_create_info.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;\n"
    "  if (formats[0] == VK_FORMAT_R32_SFLOAT && !is_3d && key.mip_max_level == 0) {\n"
    "    image_create_info.usage |= VK_IMAGE_USAGE_STORAGE_BIT;  // tt-android F18\n"
    "  }\n"
    "  image_create_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;\n"
    "  image_create_info.queueFamilyIndexCount = 0;\n"
    "  image_create_info.pQueueFamilyIndices = nullptr;\n"
    "  image_create_info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;\n"
    "  VkImageFormatListCreateInfo image_format_list_create_info;\n"), (
    "  view_key.host_swizzle = host_swizzle;\n\n  view_key.is_array = uint32_t(is_array);\n",
    "  if (tt_rb_swapped_ && vulkan_device->properties().imageViewFormatSwizzle) {  // tt-android F17\n"
    "    uint32_t swapped = 0;\n"
    "    for (uint32_t i = 0; i < 4; ++i) {\n"
    "      uint32_t source = (host_swizzle >> (3 * i)) & 0b111;\n"
    "      if (source == 0) {\n"
    "        source = 2;\n"
    "      } else if (source == 2) {\n"
    "        source = 0;\n"
    "      }\n"
    "      swapped |= source << (3 * i);\n"
    "    }\n"
    "    host_swizzle = swapped;\n"
    "  }\n"
    "  view_key.host_swizzle = host_swizzle;\n\n  view_key.is_array = uint32_t(is_array);\n"), (
    "  VulkanTexture& vulkan_texture = static_cast<VulkanTexture&>(texture);\n"
    "  TextureKey texture_key = vulkan_texture.key();\n",
    "  VulkanTexture& vulkan_texture = static_cast<VulkanTexture&>(texture);\n"
    "  if (vulkan_texture.tt_rb_swapped_) {  // tt-android F17: a normal load again\n"
    "    vulkan_texture.tt_rb_swapped_ = false;\n"
    "    tt_views_changed.store(true, std::memory_order_relaxed);\n"
    "  }\n"
    "  TextureKey texture_key = vulkan_texture.key();\n"), (
    "VkImageView VulkanTextureCache::VulkanTexture::GetView(bool is_signed, uint32_t host_swizzle,\n",
    "VkImage VulkanTextureCache::TtDirectResolveTarget(uint32_t base_page, uint32_t width,\n"
    "                                                  uint32_t height, uint32_t endian,\n"
    "                                                  bool rb_swapped, VkExtent2D& extent_out,\n"
    "                                                  xenos::TextureFormat format) {\n"
    "  if (tt_views_changed.exchange(false, std::memory_order_relaxed)) ResetTextureBindings();\n"
    "  if (!command_processor_.GetVulkanDevice()->properties().imageViewFormatSwizzle) {\n"
    "    return VK_NULL_HANDLE;\n"
    "  }\n"
    "  VulkanTexture* found = nullptr;\n"
    "  for (const auto& pair : textures_) {\n"
    "    const TextureKey& key = pair.first;\n"
    "    if (key.base_page != base_page) continue;\n"
    "    // Anything else looking at this memory needs the bytes: keep the normal path.\n"
    "    if (found || key.format != format ||\n"
    "        (format == xenos::TextureFormat::k_24_8 &&\n"
    "         GetHostFormatPair(key).format_unsigned.format != VK_FORMAT_R32_SFLOAT) ||\n"
    "        key.dimension != xenos::DataDimension::k2DOrStacked || key.depth_or_array_size_minus_1 ||\n"
    "        key.mip_max_level || key.scaled_resolve || key.signed_separate ||\n"
    "        uint32_t(key.endianness) != endian || uint32_t(key.width_minus_1) + 1 > width ||\n"
    "        uint32_t(key.height_minus_1) + 1 > height) {\n"
    "      return VK_NULL_HANDLE;\n"
    "    }\n"
    "    found = static_cast<VulkanTexture*>(pair.second.get());\n"
    "  }\n"
    "  if (!found) return VK_NULL_HANDLE;\n"
    "  {\n"
    "    auto global_lock = global_critical_region_.Acquire();\n"
    "    if (found->base_outdated(global_lock) || found->mips_outdated(global_lock)) {\n"
    "      found->MakeUpToDateAndWatch(global_lock);\n"
    "    }\n"
    "  }\n"
    "  {\n"
    "    std::lock_guard<std::mutex> lock(tt_tex_gpu_mutex);\n"
    "    tt_tex_gpu_dirty.erase(static_cast<Texture*>(found));\n"
    "  }\n"
    "  if (found->tt_rb_swapped_ != rb_swapped) {\n"
    "    found->tt_rb_swapped_ = rb_swapped;\n"
    "    ResetTextureBindings();  // the next draw picks the other view\n"
    "  }\n"
    "  found->MarkAsUsed();\n"
    "  VulkanTexture::Usage old_usage = found->SetUsage(VulkanTexture::Usage::kTransferDestination);\n"
    "  if (old_usage != VulkanTexture::Usage::kTransferDestination) {\n"
    "    VkPipelineStageFlags src_stage_mask, dst_stage_mask;\n"
    "    VkAccessFlags src_access_mask, dst_access_mask;\n"
    "    VkImageLayout old_layout, new_layout;\n"
    "    GetTextureUsageMasks(old_usage, src_stage_mask, src_access_mask, old_layout);\n"
    "    GetTextureUsageMasks(VulkanTexture::Usage::kTransferDestination, dst_stage_mask,\n"
    "                         dst_access_mask, new_layout);\n"
    "    command_processor_.PushImageMemoryBarrier(\n"
    "        found->image(), ui::vulkan::util::InitializeSubresourceRange(), src_stage_mask,\n"
    "        dst_stage_mask, src_access_mask, dst_access_mask, old_layout, new_layout);\n"
    "  }\n"
    "  extent_out.width = uint32_t(found->key().width_minus_1) + 1;\n"
    "  extent_out.height = uint32_t(found->key().height_minus_1) + 1;\n"
    "  return found->image();\n"
    "}\n"
    "\n"
    "VkImageView VulkanTextureCache::VulkanTexture::GetView(bool is_signed, uint32_t host_swizzle,\n"),
    prelude="// tt-android F17: a texture switched between its normal and red/blue-swapped view.\n"
            "static std::atomic<bool> tt_views_changed{false};\n",
    includes=("#include <atomic>",))
edit("src/graphics/vulkan/render_target_cache.cpp", (
    "  image_create_info.usage = VK_IMAGE_USAGE_SAMPLED_BIT;\n"
    "  image_create_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;\n",
    "  image_create_info.usage = VK_IMAGE_USAGE_SAMPLED_BIT |\n"
    "                            VK_IMAGE_USAGE_TRANSFER_SRC_BIT;  // tt-android F17\n"
    "  image_create_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;\n"), (
    "  // Copying.\n  bool copied = false;\n",
    "  // tt-android F17: plain colour resolve -> one image copy into the host texture.\n"
    "  static const bool tt_direct = std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr;\n"
    "  if (tt_direct && resolve_info.copy_dest_extent_length && !resolve_info.IsCopyingDepth() &&\n"
    "      !draw_resolution_scaled && GetPath() == Path::kHostRenderTargets) {\n"
    "    const draw_util::ResolveEdramInfo e = resolve_info.color_edram_info;\n"
    "    const reg::RB_COPY_DEST_INFO d = resolve_info.copy_dest_info;\n"
    "    const draw_util::ResolveCoordinateInfo c = resolve_info.coordinate_info;\n"
    "    const draw_util::ResolveCopyDestCoordinateInfo dc = resolve_info.copy_dest_coordinate_info;\n"
    "    // A 4x render target resolved with all four samples averaged (the game's 2x2\n"
    "    // downsample) goes through vkCmdResolveImage the same way (v91).\n"
    "    const bool tt_4x = e.msaa_samples == xenos::MsaaSamples::k4X &&\n"
    "                       resolve_info.rb_copy_control.copy_sample_select ==\n"
    "                           xenos::CopySampleSelect::k0123;\n"
    "    if (!resolve_info.IsCopyingDepth()) {\n"
    "      __atomic_fetch_add(&tt_work_stats[15], 1, __ATOMIC_RELAXED);  // colour resolves\n"
    "    }\n"
    "    if ((e.msaa_samples == xenos::MsaaSamples::k1X || tt_4x) &&\n"
    "        e.format == uint32_t(xenos::ColorRenderTargetFormat::k_8_8_8_8) && !e.format_is_64bpp &&\n"
    "        d.copy_dest_format == xenos::ColorFormat::k_8_8_8_8 && !d.copy_dest_array &&\n"
    "        !d.copy_dest_exp_bias && !c.edram_offset_x_div_8 && !c.edram_offset_y_div_8 &&\n"
    "        !dc.offset_x_div_8 && !dc.offset_y_div_8 &&\n"
    "        resolve_info.copy_dest_base == resolve_info.copy_dest_extent_start &&\n"
    "        !(resolve_info.copy_dest_base & 0xFFF)) {\n"
    "      uint32_t dump_base, dump_row_length_used, dump_rows, dump_pitch;\n"
    "      resolve_info.GetCopyEdramTileSpan(dump_base, dump_row_length_used, dump_rows, dump_pitch);\n"
    "      GetResolveCopyRectanglesToDump(dump_base, dump_row_length_used, dump_rows, dump_pitch,\n"
    "                                     dump_rectangles_);\n"
    "      if (dump_rectangles_.size() == 1 && dump_rectangles_[0].render_target &&\n"
    "          !dump_rectangles_[0].row_first && !dump_rectangles_[0].row_first_start &&\n"
    "          dump_rectangles_[0].rows == dump_rows) {\n"
    "        auto& rt = *static_cast<VulkanRenderTarget*>(dump_rectangles_[0].render_target);\n"
    "        const RenderTargetKey rt_key = rt.key();\n"
    "        const uint32_t width = uint32_t(c.width_div_8) * 8;\n"
    "        const uint32_t height = resolve_info.height_div_8 * 8;\n"
    "        VkExtent2D extent = {};\n"
    "        VkImage dest = VK_NULL_HANDLE;\n"
    "        if (!rt_key.is_depth && rt_key.msaa_samples == e.msaa_samples &&\n"
    "            rt_key.resource_format == uint32_t(xenos::ColorRenderTargetFormat::k_8_8_8_8) &&\n"
    "            rt_key.base_tiles == dump_base && rt_key.pitch_tiles_at_32bpp == dump_pitch &&\n"
    "            width <= dump_pitch * (tt_4x ? 40 : 80)) {\n"
    "          dest = texture_cache.TtDirectResolveTarget(resolve_info.copy_dest_base >> 12, width,\n"
    "                                                     height, uint32_t(d.copy_dest_endian),\n"
    "                                                     d.copy_dest_swap != 0, extent);\n"
    "        }\n"
    "        if (dest != VK_NULL_HANDLE) {\n"
    "          command_processor_.PushImageMemoryBarrier(\n"
    "              rt.image(), ui::vulkan::util::InitializeSubresourceRange(VK_IMAGE_ASPECT_COLOR_BIT),\n"
    "              rt.current_stage_mask(), VK_PIPELINE_STAGE_TRANSFER_BIT, rt.current_access_mask(),\n"
    "              VK_ACCESS_TRANSFER_READ_BIT, rt.current_layout(),\n"
    "              VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);\n"
    "          rt.SetUsage(VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_READ_BIT,\n"
    "                      VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);\n"
    "          command_processor_.SubmitBarriers(true);\n"
    "          VkImageCopy region = {};\n"
    "          region.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;\n"
    "          region.srcSubresource.layerCount = 1;\n"
    "          region.dstSubresource = region.srcSubresource;\n"
    "          region.extent.width = extent.width;\n"
    "          region.extent.height = extent.height;\n"
    "          region.extent.depth = 1;\n"
    "          command_buffer.TtCmdCopyImage(rt.image(), dest, region, tt_4x);\n"
    "          __atomic_fetch_add(&tt_work_stats[14], 1, __ATOMIC_RELAXED);\n"
    "          resolve_info.copy_dest_extent_length = 0;  // done; clears still happen below\n"
    "        }\n"
    "      }\n"
    "    }\n"
    "  }\n"
    "  // tt-android F18: plain depth resolve (D24S8, no MSAA) -> the R32F\n"
    "  // host texture, written by a small compute shader that reads the host depth target.\n"
    "  static const bool tt_depth = std::getenv(\"TT_DRAW_ALL_PASSES\") == nullptr;\n"
    "  if (tt_depth && resolve_info.copy_dest_extent_length && resolve_info.IsCopyingDepth() &&\n"
    "      !draw_resolution_scaled && GetPath() == Path::kHostRenderTargets) {\n"
    "    static const uint32_t tt_depth_copy_cs[] = {\n"
    "      0x07230203, 0x00010000, 0x0008000A, 0x00000032, 0x00000000, 0x00020011,\n"
    "      0x00000001, 0x00020011, 0x00000032, 0x0006000B, 0x00000001, 0x4C534C47,\n"
    "      0x6474732E, 0x3035342E, 0x00000000, 0x0003000E, 0x00000000, 0x00000001,\n"
    "      0x0006000F, 0x00000005, 0x00000004, 0x6E69616D, 0x00000000, 0x0000000D,\n"
    "      0x00060010, 0x00000004, 0x00000011, 0x00000008, 0x00000008, 0x00000001,\n"
    "      0x00030003, 0x00000002, 0x000001C2, 0x000B0004, 0x455F4C47, 0x735F5458,\n"
    "      0x6C706D61, 0x656C7265, 0x745F7373, 0x75747865, 0x665F6572, 0x74636E75,\n"
    "      0x736E6F69, 0x00000000, 0x00040005, 0x00000004, 0x6E69616D, 0x00000000,\n"
    "      0x00030005, 0x00000009, 0x00000070, 0x00080005, 0x0000000D, 0x475F6C67,\n"
    "      0x61626F6C, 0x766E496C, 0x7461636F, 0x496E6F69, 0x00000044, 0x00040005,\n"
    "      0x00000016, 0x645F7474, 0x00747365, 0x00060005, 0x00000024, 0x735F7474,\n"
    "      0x6372756F, 0x65645F65, 0x00687470, 0x00040047, 0x0000000D, 0x0000000B,\n"
    "      0x0000001C, 0x00040047, 0x00000016, 0x00000022, 0x00000001, 0x00040047,\n"
    "      0x00000016, 0x00000021, 0x00000000, 0x00030047, 0x00000016, 0x00000019,\n"
    "      0x00040047, 0x00000024, 0x00000022, 0x00000000, 0x00040047, 0x00000024,\n"
    "      0x00000021, 0x00000000, 0x00040047, 0x00000031, 0x0000000B, 0x00000019,\n"
    "      0x00020013, 0x00000002, 0x00030021, 0x00000003, 0x00000002, 0x00040015,\n"
    "      0x00000006, 0x00000020, 0x00000001, 0x00040017, 0x00000007, 0x00000006,\n"
    "      0x00000002, 0x00040020, 0x00000008, 0x00000007, 0x00000007, 0x00040015,\n"
    "      0x0000000A, 0x00000020, 0x00000000, 0x00040017, 0x0000000B, 0x0000000A,\n"
    "      0x00000003, 0x00040020, 0x0000000C, 0x00000001, 0x0000000B, 0x0004003B,\n"
    "      0x0000000C, 0x0000000D, 0x00000001, 0x00040017, 0x0000000E, 0x0000000A,\n"
    "      0x00000002, 0x00030016, 0x00000013, 0x00000020, 0x00090019, 0x00000014,\n"
    "      0x00000013, 0x00000001, 0x00000000, 0x00000000, 0x00000000, 0x00000002,\n"
    "      0x00000003, 0x00040020, 0x00000015, 0x00000000, 0x00000014, 0x0004003B,\n"
    "      0x00000015, 0x00000016, 0x00000000, 0x00020014, 0x00000019, 0x00040017,\n"
    "      0x0000001A, 0x00000019, 0x00000002, 0x00090019, 0x00000022, 0x00000013,\n"
    "      0x00000001, 0x00000000, 0x00000000, 0x00000000, 0x00000001, 0x00000000,\n"
    "      0x00040020, 0x00000023, 0x00000000, 0x00000022, 0x0004003B, 0x00000023,\n"
    "      0x00000024, 0x00000000, 0x0004002B, 0x00000006, 0x00000027, 0x00000000,\n"
    "      0x00040017, 0x00000028, 0x00000013, 0x00000004, 0x0004002B, 0x0000000A,\n"
    "      0x0000002A, 0x00000000, 0x0004002B, 0x00000013, 0x0000002C, 0x00000000,\n"
    "      0x0004002B, 0x00000013, 0x0000002D, 0x3F800000, 0x0004002B, 0x0000000A,\n"
    "      0x0000002F, 0x00000008, 0x0004002B, 0x0000000A, 0x00000030, 0x00000001,\n"
    "      0x0006002C, 0x0000000B, 0x00000031, 0x0000002F, 0x0000002F, 0x00000030,\n"
    "      0x00050036, 0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200F8,\n"
    "      0x00000005, 0x0004003B, 0x00000008, 0x00000009, 0x00000007, 0x0004003D,\n"
    "      0x0000000B, 0x0000000F, 0x0000000D, 0x0007004F, 0x0000000E, 0x00000010,\n"
    "      0x0000000F, 0x0000000F, 0x00000000, 0x00000001, 0x0004007C, 0x00000007,\n"
    "      0x00000011, 0x00000010, 0x0003003E, 0x00000009, 0x00000011, 0x0004003D,\n"
    "      0x00000007, 0x00000012, 0x00000009, 0x0004003D, 0x00000014, 0x00000017,\n"
    "      0x00000016, 0x00040068, 0x00000007, 0x00000018, 0x00000017, 0x000500AF,\n"
    "      0x0000001A, 0x0000001B, 0x00000012, 0x00000018, 0x0004009A, 0x00000019,\n"
    "      0x0000001C, 0x0000001B, 0x000300F7, 0x0000001E, 0x00000000, 0x000400FA,\n"
    "      0x0000001C, 0x0000001D, 0x0000001E, 0x000200F8, 0x0000001D, 0x000100FD,\n"
    "      0x000200F8, 0x0000001E, 0x0004003D, 0x00000014, 0x00000020, 0x00000016,\n"
    "      0x0004003D, 0x00000007, 0x00000021, 0x00000009, 0x0004003D, 0x00000022,\n"
    "      0x00000025, 0x00000024, 0x0004003D, 0x00000007, 0x00000026, 0x00000009,\n"
    "      0x0007005F, 0x00000028, 0x00000029, 0x00000025, 0x00000026, 0x00000002,\n"
    "      0x00000027, 0x00050051, 0x00000013, 0x0000002B, 0x00000029, 0x00000000,\n"
    "      0x00070050, 0x00000028, 0x0000002E, 0x0000002B, 0x0000002C, 0x0000002C,\n"
    "      0x0000002D, 0x00040063, 0x00000020, 0x00000021, 0x0000002E, 0x000100FD,\n"
    "      0x00010038,\n"
    "    };\n"
    "    struct TtDepthCopy {\n"
    "      bool tried = false;\n"
    "      VkDescriptorSetLayout dest_layout = VK_NULL_HANDLE;\n"
    "      VkPipelineLayout layout = VK_NULL_HANDLE;\n"
    "      VkPipeline pipeline = VK_NULL_HANDLE;\n"
    "      VkDescriptorPool pool = VK_NULL_HANDLE;\n"
    "      std::unordered_map<VkImage, VkDescriptorSet> sets;\n"
    "    };\n"
    "    static TtDepthCopy tt;\n"
    "    const ui::vulkan::VulkanDevice* const tt_device = command_processor_.GetVulkanDevice();\n"
    "    const ui::vulkan::VulkanDevice::Functions& tt_dfn = tt_device->functions();\n"
    "    if (!tt.tried) {\n"
    "      tt.tried = true;\n"
    "      VkDescriptorSetLayoutBinding binding = {};\n"
    "      binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;\n"
    "      binding.descriptorCount = 1;\n"
    "      binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;\n"
    "      VkDescriptorSetLayoutCreateInfo layout_info = {};\n"
    "      layout_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;\n"
    "      layout_info.bindingCount = 1;\n"
    "      layout_info.pBindings = &binding;\n"
    "      VkDescriptorPoolSize pool_size = {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 64};\n"
    "      VkDescriptorPoolCreateInfo pool_info = {};\n"
    "      pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;\n"
    "      pool_info.maxSets = 64;\n"
    "      pool_info.poolSizeCount = 1;\n"
    "      pool_info.pPoolSizes = &pool_size;\n"
    "      if (tt_dfn.vkCreateDescriptorSetLayout(tt_device->device(), &layout_info, nullptr,\n"
    "                                             &tt.dest_layout) == VK_SUCCESS &&\n"
    "          tt_dfn.vkCreateDescriptorPool(tt_device->device(), &pool_info, nullptr, &tt.pool) ==\n"
    "              VK_SUCCESS) {\n"
    "        VkDescriptorSetLayout set_layouts[] = {descriptor_set_layout_sampled_image_x2_,\n"
    "                                               tt.dest_layout};\n"
    "        VkPipelineLayoutCreateInfo pipeline_layout_info = {};\n"
    "        pipeline_layout_info.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;\n"
    "        pipeline_layout_info.setLayoutCount = 2;\n"
    "        pipeline_layout_info.pSetLayouts = set_layouts;\n"
    "        if (tt_dfn.vkCreatePipelineLayout(tt_device->device(), &pipeline_layout_info, nullptr,\n"
    "                                          &tt.layout) == VK_SUCCESS) {\n"
    "          tt.pipeline = ui::vulkan::util::CreateComputePipeline(\n"
    "              tt_device, tt.layout, tt_depth_copy_cs, sizeof(tt_depth_copy_cs));\n"
    "        }\n"
    "      }\n"
    "      if (tt.pipeline == VK_NULL_HANDLE) {\n"
    "        REXGPU_ERROR(\"tt-android: direct depth resolve pipeline not created; using the normal path\");\n"
    "      }\n"
    "    }\n"
    "    const draw_util::ResolveEdramInfo e = resolve_info.depth_edram_info;\n"
    "    const reg::RB_COPY_DEST_INFO d = resolve_info.copy_dest_info;\n"
    "    const draw_util::ResolveCoordinateInfo c = resolve_info.coordinate_info;\n"
    "    const draw_util::ResolveCopyDestCoordinateInfo dc = resolve_info.copy_dest_coordinate_info;\n"
    "    if (tt.pipeline != VK_NULL_HANDLE && e.msaa_samples == xenos::MsaaSamples::k1X &&\n"
    "        e.format == uint32_t(xenos::DepthRenderTargetFormat::kD24S8) &&\n"
    "        uint32_t(d.copy_dest_format) == uint32_t(xenos::TextureFormat::k_24_8) &&\n"
    "        !d.copy_dest_array && !c.edram_offset_x_div_8 && !c.edram_offset_y_div_8 &&\n"
    "        !dc.offset_x_div_8 && !dc.offset_y_div_8 &&\n"
    "        resolve_info.copy_dest_base == resolve_info.copy_dest_extent_start &&\n"
    "        !(resolve_info.copy_dest_base & 0xFFF)) {\n"
    "      uint32_t dump_base, dump_row_length_used, dump_rows, dump_pitch;\n"
    "      resolve_info.GetCopyEdramTileSpan(dump_base, dump_row_length_used, dump_rows, dump_pitch);\n"
    "      GetResolveCopyRectanglesToDump(dump_base, dump_row_length_used, dump_rows, dump_pitch,\n"
    "                                     dump_rectangles_);\n"
    "      if (dump_rectangles_.size() == 1 && dump_rectangles_[0].render_target &&\n"
    "          !dump_rectangles_[0].row_first && !dump_rectangles_[0].row_first_start &&\n"
    "          dump_rectangles_[0].rows == dump_rows) {\n"
    "        auto& rt = *static_cast<VulkanRenderTarget*>(dump_rectangles_[0].render_target);\n"
    "        const RenderTargetKey rt_key = rt.key();\n"
    "        const uint32_t width = uint32_t(c.width_div_8) * 8;\n"
    "        const uint32_t height = resolve_info.height_div_8 * 8;\n"
    "        VkExtent2D extent = {};\n"
    "        VkImage dest = VK_NULL_HANDLE;\n"
    "        if (rt_key.is_depth && rt_key.msaa_samples == xenos::MsaaSamples::k1X &&\n"
    "            rt_key.resource_format == uint32_t(xenos::DepthRenderTargetFormat::kD24S8) &&\n"
    "            rt_key.base_tiles == dump_base && rt_key.pitch_tiles_at_32bpp == dump_pitch &&\n"
    "            width <= dump_pitch * 80) {\n"
    "          dest = texture_cache.TtDirectResolveTarget(resolve_info.copy_dest_base >> 12, width,\n"
    "                                                     height, uint32_t(d.copy_dest_endian), false,\n"
    "                                                     extent, xenos::TextureFormat::k_24_8);\n"
    "        }\n"
    "        VkDescriptorSet dest_set = VK_NULL_HANDLE;\n"
    "        if (dest != VK_NULL_HANDLE) {\n"
    "          auto it = tt.sets.find(dest);\n"
    "          if (it != tt.sets.end()) {\n"
    "            dest_set = it->second;\n"
    "          } else if (tt.sets.size() < 64) {\n"
    "            VkImageViewCreateInfo view_info = {};\n"
    "            view_info.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;\n"
    "            view_info.image = dest;\n"
    "            view_info.viewType = VK_IMAGE_VIEW_TYPE_2D;\n"
    "            view_info.format = VK_FORMAT_R32_SFLOAT;\n"
    "            view_info.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;\n"
    "            view_info.subresourceRange.levelCount = 1;\n"
    "            view_info.subresourceRange.layerCount = 1;\n"
    "            VkImageView view = VK_NULL_HANDLE;\n"
    "            VkDescriptorSetAllocateInfo allocate_info = {};\n"
    "            allocate_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;\n"
    "            allocate_info.descriptorPool = tt.pool;\n"
    "            allocate_info.descriptorSetCount = 1;\n"
    "            allocate_info.pSetLayouts = &tt.dest_layout;\n"
    "            if (tt_dfn.vkCreateImageView(tt_device->device(), &view_info, nullptr, &view) ==\n"
    "                    VK_SUCCESS &&\n"
    "                tt_dfn.vkAllocateDescriptorSets(tt_device->device(), &allocate_info, &dest_set) ==\n"
    "                    VK_SUCCESS) {\n"
    "              VkDescriptorImageInfo image_info = {};\n"
    "              image_info.imageView = view;\n"
    "              image_info.imageLayout = VK_IMAGE_LAYOUT_GENERAL;\n"
    "              VkWriteDescriptorSet write = {};\n"
    "              write.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;\n"
    "              write.dstSet = dest_set;\n"
    "              write.descriptorCount = 1;\n"
    "              write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;\n"
    "              write.pImageInfo = &image_info;\n"
    "              tt_dfn.vkUpdateDescriptorSets(tt_device->device(), 1, &write, 0, nullptr);\n"
    "              tt.sets.emplace(dest, dest_set);\n"
    "            } else {\n"
    "              dest_set = VK_NULL_HANDLE;\n"
    "            }\n"
    "          }\n"
    "        }\n"
    "        if (dest_set != VK_NULL_HANDLE) {\n"
    "          // The texture was made a transfer destination; a shader writes it in GENERAL.\n"
    "          const VkImageSubresourceRange dest_range =\n"
    "              ui::vulkan::util::InitializeSubresourceRange(VK_IMAGE_ASPECT_COLOR_BIT);\n"
    "          command_processor_.PushImageMemoryBarrier(\n"
    "              dest, dest_range, VK_PIPELINE_STAGE_TRANSFER_BIT,\n"
    "              VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,\n"
    "              VK_ACCESS_SHADER_WRITE_BIT, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,\n"
    "              VK_IMAGE_LAYOUT_GENERAL);\n"
    "          command_processor_.PushImageMemoryBarrier(\n"
    "              rt.image(),\n"
    "              ui::vulkan::util::InitializeSubresourceRange(VK_IMAGE_ASPECT_DEPTH_BIT |\n"
    "                                                           VK_IMAGE_ASPECT_STENCIL_BIT),\n"
    "              rt.current_stage_mask(), VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,\n"
    "              rt.current_access_mask(), VK_ACCESS_SHADER_READ_BIT, rt.current_layout(),\n"
    "              VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);\n"
    "          rt.SetUsage(VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,\n"
    "                      VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);\n"
    "          command_processor_.BindExternalComputePipeline(tt.pipeline);\n"
    "          VkDescriptorSet sets[] = {rt.GetDescriptorSetTransferSource(), dest_set};\n"
    "          command_buffer.CmdVkBindDescriptorSets(VK_PIPELINE_BIND_POINT_COMPUTE, tt.layout, 0, 2,\n"
    "                                                 sets, 0, nullptr);\n"
    "          command_processor_.SubmitBarriers(true);\n"
    "          command_buffer.CmdVkDispatch((extent.width + 7) / 8, (extent.height + 7) / 8, 1);\n"
    "          command_processor_.PushImageMemoryBarrier(\n"
    "              dest, dest_range, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,\n"
    "              VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_SHADER_WRITE_BIT,\n"
    "              VK_ACCESS_TRANSFER_WRITE_BIT, VK_IMAGE_LAYOUT_GENERAL,\n"
    "              VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);\n"
    "          __atomic_fetch_add(&tt_work_stats[12], 1, __ATOMIC_RELAXED);\n"
    "          resolve_info.copy_dest_extent_length = 0;  // done; clears still happen below\n"
    "        }\n"
    "      }\n"
    "    }\n"
    "  }\n"
    "  // Copying.\n  bool copied = false;\n"))
done.append("F17 colour resolves as one direct image copy")


# ---- D5 (v104, temporary): what stalls a frame? Logs any single pipeline creation, shader
#      translation or controller rumble call that takes 15 ms or longer, with its duration,
#      to line up with "tt: frame spike" (Retroid Pocket Duo: stutter on the first serve).
TT_SLOW = (
    "// tt-android D5: logs a slow step.\n"
    "struct TtSlowStep {\n"
    "  const char* what;\n"
    "  std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();\n"
    "  explicit TtSlowStep(const char* w) : what(w) {}\n"
    "  ~TtSlowStep() {\n"
    "    const long long ms = std::chrono::duration_cast<std::chrono::milliseconds>(\n"
    "                             std::chrono::steady_clock::now() - start)\n"
    "                             .count();\n"
    "    if (ms >= 15) TT_SLOW_LOG(\"tt-slow: {} took {} ms\", what, ms);\n"
    "  }\n"
    "};\n")
TT_SLOW_INCLUDES = ("#include <chrono>",)
edit("src/graphics/vulkan/pipeline_cache.cpp", (
    "  VkPipeline pipeline;\n"
    "  VkResult create_result = dfn.vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1,\n",
    "  VkPipeline pipeline;\n"
    "  TtSlowStep tt_slow_step(\"pipeline creation\");  // tt-android D5\n"
    "  VkResult create_result = dfn.vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1,\n"), (
    "  // Perform translation.\n"
    "  // If this fails the shader will be marked as invalid and ignored later.\n",
    "  TtSlowStep tt_slow_step(\"shader translation\");  // tt-android D5\n"
    "  // Perform translation.\n"
    "  // If this fails the shader will be marked as invalid and ignored later.\n"),
    prelude="#define TT_SLOW_LOG REXLOG_WARN\n" + TT_SLOW, includes=TT_SLOW_INCLUDES)
edit("src/input/sdl/sdl_input_driver.cpp", (
    "  return SDL_RumbleGamepad(controller->sdl, vibration->left_motor_speed,\n",
    "  TtSlowStep tt_slow_step(\"controller rumble\");  // tt-android D5\n"
    "  return SDL_RumbleGamepad(controller->sdl, vibration->left_motor_speed,\n"),
    prelude="#define TT_SLOW_LOG REXLOG_WARN\n" + TT_SLOW, includes=TT_SLOW_INCLUDES)
edit("src/graphics/vulkan/texture_cache.cpp", (
    "void VulkanTextureCache::RequestTextures(uint32_t used_texture_mask) {\n",
    "void VulkanTextureCache::RequestTextures(uint32_t used_texture_mask) {\n"
    "  TtSlowStep tt_slow_step(\"texture requests for one draw\");  // tt-android D5 (v107)\n"),
    prelude="#define TT_SLOW_LOG REXLOG_WARN\n" + TT_SLOW, includes=TT_SLOW_INCLUDES)
edit("src/graphics/shared_memory.cpp", (
    "bool SharedMemory::RequestRanges(const std::pair<uint32_t, uint32_t>* ranges, size_t count) {\n",
    "bool SharedMemory::RequestRanges(const std::pair<uint32_t, uint32_t>* ranges, size_t count) {\n"
    "  TtSlowStep tt_slow_step(\"memory upload for one draw\");  // tt-android D5 (v107)\n"),
    prelude="#define TT_SLOW_LOG REXLOG_WARN\n" + TT_SLOW, includes=TT_SLOW_INCLUDES + ("#include <rex/logging/macros.h>",))
edit(CP, (
    "bool VulkanCommandProcessor::IssueDraw(xenos::PrimitiveType prim_type, uint32_t index_count,\n"
    "                                       IndexBufferInfo* index_buffer_info,\n"
    "                                       bool major_mode_explicit) {\n",
    "bool VulkanCommandProcessor::IssueDraw(xenos::PrimitiveType prim_type, uint32_t index_count,\n"
    "                                       IndexBufferInfo* index_buffer_info,\n"
    "                                       bool major_mode_explicit) {\n"
    "  TtSlowStep tt_slow_step(\"one draw\");  // tt-android D5 (v107)\n"),
    prelude="#define TT_SLOW_LOG REXLOG_WARN\n" + TT_SLOW, includes=TT_SLOW_INCLUDES)
done.append("D5 slow-step log (pipeline creation, shader translation, rumble, texture requests, memory uploads, whole draws)")


# ---- F19 (v105): persistent Vulkan pipeline cache. The SDK creates every pipeline with no
#      VkPipelineCache. Turnip keeps its own disk cache, but Qualcomm's driver (Retroid Pocket
#      Duo) compiled everything again on every launch: stalls of 0.5-4 s in the first minute
#      with Shaders: Wait. The cache lives next to the SDK's shader storage and is written
#      every 16 new pipelines, within 3 s of any new one (v106), and at shutdown. The driver checks
#      the header itself and ignores data from another GPU or driver version.
edit("include/rex/ui/vulkan/functions/device_1_0.inc", (
    "XE_UI_VULKAN_FUNCTION(vkCreateGraphicsPipelines)\n",
    "XE_UI_VULKAN_FUNCTION(vkCreateGraphicsPipelines)\n"
    "XE_UI_VULKAN_FUNCTION(vkCreatePipelineCache)\n"
    "XE_UI_VULKAN_FUNCTION(vkDestroyPipelineCache)\n"
    "XE_UI_VULKAN_FUNCTION(vkGetPipelineCacheData)\n"))
edit("src/graphics/vulkan/pipeline_cache.cpp", (
    "  auto shader_storage_root = cache_root / \"shaders\";\n"
    "  auto shader_storage_shareable_root = shader_storage_root / \"shareable\";\n",
    "  auto shader_storage_root = cache_root / \"shaders\";\n"
    "  auto shader_storage_shareable_root = shader_storage_root / \"shareable\";\n"
    "  TtPipelineCacheOpen(command_processor_.GetVulkanDevice(),\n"
    "                      shader_storage_root / \"tt_vk_pipeline_cache.bin\");  // tt-android F19\n"), (
    "  shader_storage_cache_root_.clear();\n  shader_storage_title_id_ = 0;\n}\n",
    "  shader_storage_cache_root_.clear();\n  shader_storage_title_id_ = 0;\n"
    "  TtPipelineCacheSave(true);  // tt-android F19\n}\n"), (
    "  VkResult create_result = dfn.vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1,\n"
    "                                                         &pipeline_create_info, nullptr, &pipeline);\n",
    "  VkResult create_result = dfn.vkCreateGraphicsPipelines(device, tt_pc.cache, 1,  // F19\n"
    "                                                         &pipeline_create_info, nullptr, &pipeline);\n"
    "  if (create_result == VK_SUCCESS) TtPipelineCacheSave(false);  // tt-android F19\n"),
    prelude=(
    "// tt-android F19: persistent VkPipelineCache.\n"
    "struct TtPipelineCacheState {\n"
    "  const rex::ui::vulkan::VulkanDevice* device = nullptr;\n"
    "  VkPipelineCache cache = VK_NULL_HANDLE;\n"
    "  std::filesystem::path path;\n"
    "  std::mutex save_mutex;\n"
    "  std::atomic<uint32_t> unsaved{0};\n"
    "  std::chrono::steady_clock::time_point last_save = std::chrono::steady_clock::now();\n"
    "};\n"
    "static TtPipelineCacheState tt_pc;\n"
    "static void TtPipelineCacheSave(bool force);\n"
    "static void TtPipelineCacheOpen(const rex::ui::vulkan::VulkanDevice* device,\n"
    "                                const std::filesystem::path& path) {\n"
    "  if (tt_pc.cache != VK_NULL_HANDLE || !device) return;\n"
    "  tt_pc.device = device;\n"
    "  tt_pc.path = path;\n"
    "  std::vector<char> data;\n"
    "  if (FILE* f = rex::filesystem::OpenFile(path, \"rb\")) {\n"
    "    rex::filesystem::Seek(f, 0, SEEK_END);\n"
    "    const int64_t size = rex::filesystem::Tell(f);\n"
    "    rex::filesystem::Seek(f, 0, SEEK_SET);\n"
    "    if (size > 0 && size < (int64_t(256) << 20)) {\n"
    "      data.resize(size_t(size));\n"
    "      if (fread(data.data(), 1, data.size(), f) != data.size()) data.clear();\n"
    "    }\n"
    "    fclose(f);\n"
    "  }\n"
    "  VkPipelineCacheCreateInfo info = {};\n"
    "  info.sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO;\n"
    "  info.initialDataSize = data.size();\n"
    "  info.pInitialData = data.empty() ? nullptr : data.data();\n"
    "  const auto& dfn = device->functions();\n"
    "  if (dfn.vkCreatePipelineCache(device->device(), &info, nullptr, &tt_pc.cache) != VK_SUCCESS) {\n"
    "    info.initialDataSize = 0;\n"
    "    info.pInitialData = nullptr;\n"
    "    if (dfn.vkCreatePipelineCache(device->device(), &info, nullptr, &tt_pc.cache) != VK_SUCCESS) {\n"
    "      tt_pc.cache = VK_NULL_HANDLE;\n"
    "    }\n"
    "  }\n"
    "  if (tt_pc.cache != VK_NULL_HANDLE) {\n"
    "    // Pipelines made since the last save are written within ~3 s, so a game closed by\n"
    "    // Android (no shutdown) loses at most the last few seconds (v106).\n"
    "    std::thread([] {\n"
    "      for (;;) {\n"
    "        std::this_thread::sleep_for(std::chrono::seconds(3));\n"
    "        if (tt_pc.unsaved.load()) TtPipelineCacheSave(true);\n"
    "      }\n"
    "    }).detach();\n"
    "  }\n"
    "  REXLOG_INFO(\"tt: pipeline cache {} ({} KB loaded)\",\n"
    "              tt_pc.cache != VK_NULL_HANDLE ? \"ready\" : \"unavailable\", data.size() / 1024);\n"
    "}\n"
    "static void TtPipelineCacheSave(bool force) {\n"
    "  if (tt_pc.cache == VK_NULL_HANDLE) return;\n"
    "  const uint32_t unsaved = force ? tt_pc.unsaved.load() : tt_pc.unsaved.fetch_add(1) + 1;\n"
    "  const auto now = std::chrono::steady_clock::now();\n"
    "  if (!force && unsaved < 16 && now - tt_pc.last_save < std::chrono::seconds(15)) return;\n"
    "  if (!unsaved) return;\n"
    "  std::unique_lock<std::mutex> lock(tt_pc.save_mutex, std::try_to_lock);\n"
    "  if (!lock.owns_lock()) return;\n"
    "  tt_pc.unsaved = 0;\n"
    "  tt_pc.last_save = now;\n"
    "  const auto& dfn = tt_pc.device->functions();\n"
    "  size_t size = 0;\n"
    "  if (dfn.vkGetPipelineCacheData(tt_pc.device->device(), tt_pc.cache, &size, nullptr) !=\n"
    "          VK_SUCCESS || !size) {\n"
    "    return;\n"
    "  }\n"
    "  std::vector<char> data(size);\n"
    "  if (dfn.vkGetPipelineCacheData(tt_pc.device->device(), tt_pc.cache, &size, data.data()) !=\n"
    "      VK_SUCCESS) {\n"
    "    return;\n"
    "  }\n"
    "  std::filesystem::path temp = tt_pc.path;\n"
    "  temp += \".tmp\";\n"
    "  if (FILE* f = rex::filesystem::OpenFile(temp, \"wb\")) {\n"
    "    const bool ok = fwrite(data.data(), 1, size, f) == size;\n"
    "    fclose(f);\n"
    "    std::error_code ec;\n"
    "    if (ok) std::filesystem::rename(temp, tt_pc.path, ec);\n"
    "  }\n"
    "}\n"),
    includes=("#include <atomic>", "#include <chrono>", "#include <filesystem>", "#include <mutex>",
              "#include <thread>", "#include <vector>"))
done.append("F19 persistent Vulkan pipeline cache")


# ============================================================ RENDERER BRANCH: C1 census
# Step 1 of the own-renderer project (branch "renderer", separate test app). Records which
# features of the Xbox 360 GPU this game actually uses, so the new renderer's scope is known:
# primitive types, shaders and their features, vertex formats, texture formats and
# dimensions, render-target setups, EDRAM ownership transfers, resolves. Written to
# <logs>/census.txt every 10 s (whole session so far). Changes nothing that is drawn.
TT_CENSUS = r"""
// tt-android C1: census of the GPU features this game uses (renderer branch).
#include <cstdio>
#include <map>
#include <mutex>
#include <set>
#include <string>
namespace tt_census {
inline std::mutex& Mutex() { static std::mutex m; return m; }
inline std::map<std::string, std::map<std::string, uint64_t>>& Tables() {
  static std::map<std::string, std::map<std::string, uint64_t>> t;
  return t;
}
inline std::set<uint64_t>& Seen(const char* name) {
  static std::map<std::string, std::set<uint64_t>> s;
  return s[name];
}
inline void Count(const char* table, const std::string& key, uint64_t n = 1) {
  std::lock_guard<std::mutex> lock(Mutex());
  Tables()[table][key] += n;
}
// Counts a distinct item once (by id) under `table`, e.g. unique shaders.
inline bool Once(const char* table, uint64_t id, const std::string& key) {
  std::lock_guard<std::mutex> lock(Mutex());
  if (!Seen(table).insert(id).second) return false;
  Tables()[table][key] += 1;
  return true;
}
inline std::string Fmt(const char* format, ...) {
  char buffer[512];
  va_list args;
  va_start(args, format);
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  return buffer;
}
inline void MaybeWrite() {
  static auto last = std::chrono::steady_clock::now();
  static const auto start = last;
  const auto now = std::chrono::steady_clock::now();
  if (now - last < std::chrono::seconds(10)) return;
  last = now;
  const char* dir = std::getenv("TT_PROFILE");
  if (!dir) return;
  std::lock_guard<std::mutex> lock(Mutex());
  FILE* f = std::fopen((std::string(dir) + "/census.txt").c_str(), "w");
  if (!f) return;
  std::fprintf(f, "GPU feature census, %lld s of session so far (counts are draws unless the table "
                  "says otherwise)\n",
               (long long)std::chrono::duration_cast<std::chrono::seconds>(now - start).count());
  for (const auto& [table, rows] : Tables()) {
    std::vector<std::pair<uint64_t, std::string>> sorted;
    for (const auto& [key, count] : rows) sorted.emplace_back(count, key);
    std::sort(sorted.rbegin(), sorted.rend());
    std::fprintf(f, "\n== %s (%zu distinct)\n", table.c_str(), sorted.size());
    size_t shown = 0;
    for (const auto& [count, key] : sorted) {
      if (++shown > 80) { std::fprintf(f, "  ... %zu more\n", sorted.size() - 80); break; }
      std::fprintf(f, "  %10llu  %s\n", (unsigned long long)count, key.c_str());
    }
  }
  std::fclose(f);
}
}  // namespace tt_census
"""
TT_CENSUS_INCLUDES = ("#include <algorithm>", "#include <chrono>", "#include <cstdarg>",
                      "#include <cstdio>", "#include <cstdlib>", "#include <vector>")
edit(CP, (
    "  uint32_t ps_param_gen_pos = UINT32_MAX;\n"
    "  uint32_t interpolator_mask =\n",
    "  {  // tt-android C1: census of this draw\n"
    "    using namespace tt_census;\n"
    "    const auto surface = regs.Get<reg::RB_SURFACE_INFO>();\n"
    "    Count(\"primitive types\", Fmt(\"type %u, %s\", uint32_t(prim_type),\n"
    "                                  index_buffer_info ? \"indexed\" : \"not indexed\"));\n"
    "    if (memexport_used_vertex || memexport_used_pixel) {\n"
    "      Count(\"memexport draws\", memexport_used_pixel ? \"pixel shader\" : \"vertex shader\");\n"
    "    }\n"
    "    Once(\"vertex shaders (unique)\", vertex_shader->ucode_data_hash(),\n"
    "         Fmt(\"%zu vertex bindings%s\", vertex_shader->vertex_bindings().size(),\n"
    "             vertex_shader->uses_register_dynamic_addressing() ? \", dynamic addressing\" : \"\"));\n"
    "    if (pixel_shader) {\n"
    "      Once(\"pixel shaders (unique)\", pixel_shader->ucode_data_hash(),\n"
    "           Fmt(\"%zu textures%s%s%s\", pixel_shader->texture_bindings().size(),\n"
    "               pixel_shader->kills_pixels() ? \", kills pixels\" : \"\",\n"
    "               pixel_shader->writes_depth() ? \", writes depth\" : \"\",\n"
    "               pixel_shader->uses_register_dynamic_addressing() ? \", dynamic addressing\" : \"\"));\n"
    "    } else {\n"
    "      Count(\"draws without a pixel shader\", \"depth only\");\n"
    "    }\n"
    "    for (const auto& binding : vertex_shader->vertex_bindings()) {\n"
    "      for (const auto& attribute : binding.attributes) {\n"
    "        Count(\"vertex formats (attributes fetched)\",\n"
    "              Fmt(\"format %u\", uint32_t(attribute.fetch_instr.attributes.data_format)));\n"
    "      }\n"
    "    }\n"
    "    for (const VulkanShader* shader : {vertex_shader, pixel_shader}) {\n"
    "      if (!shader) continue;\n"
    "      for (const auto& binding : shader->texture_bindings()) {\n"
    "        const xenos::xe_gpu_texture_fetch_t fetch = regs.GetTextureFetch(binding.fetch_constant);\n"
    "        Count(\"textures sampled (format, fetch dimension, size, tiled, mips)\",\n"
    "              Fmt(\"%s: format %u, dim %u, %ux%u, tiled %u, mips %u-%u\",\n"
    "                  shader == vertex_shader ? \"VS\" : \"PS\", uint32_t(fetch.format),\n"
    "                  uint32_t(binding.fetch_instr.dimension), fetch.size_2d.width + 1,\n"
    "                  fetch.size_2d.height + 1, uint32_t(fetch.tiled), uint32_t(fetch.mip_min_level),\n"
    "                  uint32_t(fetch.mip_max_level)));\n"
    "      }\n"
    "    }\n"
    "    const uint32_t color_mask =\n"
    "        pixel_shader ? draw_util::GetNormalizedColorMask(regs, pixel_shader->writes_color_targets())\n"
    "                     : 0;\n"
    "    std::string targets = Fmt(\"pitch %u, msaa %u:\", uint32_t(surface.surface_pitch),\n"
    "                              uint32_t(surface.msaa_samples));\n"
    "    for (uint32_t i = 0; i < 4; ++i) {\n"
    "      if (!((color_mask >> (i * 4)) & 0xF)) continue;\n"
    "      const auto color_info = regs.Get<reg::RB_COLOR_INFO>(reg::RB_COLOR_INFO::rt_register_indices[i]);\n"
    "      targets += Fmt(\" C%u fmt %u base %u\", i, uint32_t(color_info.color_format),\n"
    "                     uint32_t(color_info.color_base));\n"
    "    }\n"
    "    if (normalized_depth_control.z_enable || normalized_depth_control.stencil_enable) {\n"
    "      const auto depth_info = regs.Get<reg::RB_DEPTH_INFO>();\n"
    "      targets += Fmt(\" D fmt %u base %u%s\", uint32_t(depth_info.depth_format),\n"
    "                     uint32_t(depth_info.depth_base),\n"
    "                     normalized_depth_control.z_write_enable ? \" write\" : \"\");\n"
    "    }\n"
    "    Count(\"render-target setups\", targets);\n"
    "    MaybeWrite();\n"
    "  }\n"
    "  uint32_t ps_param_gen_pos = UINT32_MAX;\n"
    "  uint32_t interpolator_mask =\n"),
    prelude=TT_CENSUS, includes=TT_CENSUS_INCLUDES)
edit("src/graphics/vulkan/render_target_cache.cpp", (
    "  // Nothing to copy/clear.\n",
    "  {  // tt-android C1: census of resolves\n"
    "    using namespace tt_census;\n"
    "    const auto& d = resolve_info.copy_dest_info;\n"
    "    const auto& e = resolve_info.IsCopyingDepth() ? resolve_info.depth_edram_info\n"
    "                                                  : resolve_info.color_edram_info;\n"
    "    Count(\"resolves (source -> destination)\",\n"
    "          Fmt(\"%s fmt %u msaa %u pitch %u tiles -> %ux%u dest fmt %u endian %u swap %u, %s, \"\n"
    "              \"sample select %u%s\",\n"
    "              resolve_info.IsCopyingDepth() ? \"depth\" : \"colour\", uint32_t(e.format),\n"
    "              uint32_t(e.msaa_samples), uint32_t(e.pitch_tiles),\n"
    "              uint32_t(resolve_info.coordinate_info.width_div_8) * 8,\n"
    "              resolve_info.height_div_8 * 8, uint32_t(d.copy_dest_format),\n"
    "              uint32_t(d.copy_dest_endian), uint32_t(d.copy_dest_swap),\n"
    "              resolve_info.copy_dest_extent_length ? \"copy\" : \"clear only\",\n"
    "              uint32_t(resolve_info.rb_copy_control.copy_sample_select),\n"
    "              resolve_info.rb_copy_control.color_clear_enable ||\n"
    "                      resolve_info.rb_copy_control.depth_clear_enable\n"
    "                  ? \", clears\"\n"
    "                  : \"\"));\n"
    "  }\n"
    "  // Nothing to copy/clear.\n"), (
    "  bool resolve_clear_needed = render_target_resolve_clear_values && resolve_clear_rectangle;\n",
    "  for (uint32_t i = 0; i < render_target_count; ++i) {  // tt-android C1: census of transfers\n"
    "    if (!render_targets[i]) continue;\n"
    "    for (const Transfer& transfer : render_target_transfers[i]) {\n"
    "      const RenderTargetKey dk = render_targets[i]->key();\n"
    "      const RenderTargetKey sk = transfer.source->key();\n"
    "      tt_census::Count(\"EDRAM ownership transfers (source -> destination)\",\n"
    "                       tt_census::Fmt(\"%s fmt %u msaa %u pitch %u -> %s fmt %u msaa %u pitch %u\",\n"
    "                                      sk.is_depth ? \"depth\" : \"colour\", sk.resource_format,\n"
    "                                      uint32_t(sk.msaa_samples), sk.pitch_tiles_at_32bpp,\n"
    "                                      dk.is_depth ? \"depth\" : \"colour\", dk.resource_format,\n"
    "                                      uint32_t(dk.msaa_samples), dk.pitch_tiles_at_32bpp));\n"
    "    }\n"
    "  }\n"
    "  bool resolve_clear_needed = render_target_resolve_clear_values && resolve_clear_rectangle;\n"),
    prelude=TT_CENSUS, includes=TT_CENSUS_INCLUDES)
done.append("C1 census (renderer branch)")

print("\n".join(done))
