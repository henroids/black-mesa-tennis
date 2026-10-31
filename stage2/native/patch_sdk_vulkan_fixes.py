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
  F8        presenter: swapchain acquire waited on the CPU, present without semaphores
  F9        skip re-converting textures whose guest bytes didn't change          ~+1 FPS
  F10       skip two depth-only passes with no visible effect (640/1120 wide)     ~+2 FPS
  F11       skip a full-size pass that writes neither colour nor depth (~96 draws) ~+0.5 FPS

DIAGNOSTICS (counters read by tt_profiler.cpp via dlsym; cheap atomic adds)
  D1  tt_frame_stats  [0] guest vblanks [1] CP wait-for-memory sleeps [2] their ns [3] swaps
  D2  tt_work_stats   [0] draws [1] resolves [2] texture loads [3] texture bytes
                      [6] upload calls [7] upload bytes [8] draws skipped by F10/F11
  D3  tt_tex_stats    [0] texture loads asked [1] skipped (unchanged) [2] GPU-written
                      [3] bytes hashed
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
#      refresh, and a present that waits on a semaphore can idle the whole shared queue.
#      The UI thread now waits for the acquire and for its own paint on the CPU, and
#      submits / presents with no semaphores, so nothing on the GPU queue waits for vsync.
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
    "  submit_info.signalSemaphoreCount = 1;\n  submit_info.pSignalSemaphores = &present_semaphore;\n",
    "  submit_info.signalSemaphoreCount = 0;  // tt-android F8: present waits on the CPU\n"
    "  submit_info.pSignalSemaphores = nullptr;\n"), (
    "  VkPresentInfoKHR present_info;\n  present_info.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;\n"
    "  present_info.pNext = nullptr;\n  present_info.waitSemaphoreCount = 1;\n"
    "  present_info.pWaitSemaphores = &present_semaphore;\n",
    "  // tt-android F8: wait for the paint on the CPU, then present without semaphores.\n"
    "  paint_context_.submission_tracker.AwaitSubmissionCompletion(\n"
    "      paint_context_.submission_tracker.GetCurrentSubmission() - 1);\n"
    "  VkPresentInfoKHR present_info;\n  present_info.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;\n"
    "  present_info.pNext = nullptr;\n  present_info.waitSemaphoreCount = 0;\n"
    "  present_info.pWaitSemaphores = nullptr;\n"), (
    # the semaphores above are no longer used
    "  VkPipelineStageFlags acquire_semaphore_wait_stage = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;\n", ""), (
    "  VkSemaphore present_semaphore = paint_submission.present_semaphore();\n", ""))
done.append("F8 presenter acquires and presents without GPU-side semaphore waits")

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
    struct State { TextureKey key; uint64_t base = 0, mips = 0; bool has_base = false, has_mips = false; };
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
    uint64_t hash_base = 0, hash_mips = 0;
    if (load_base && texture.GetGuestBaseSize()) {
      hash_base = XXH3_64bits(mem.TranslatePhysical(uint32_t(key.base_page) << 12),
                              texture.GetGuestBaseSize());
      __atomic_fetch_add(&tt_tex_stats[3], uint64_t(texture.GetGuestBaseSize()), __ATOMIC_RELAXED);
    }
    if (load_mips && texture.GetGuestMipsSize()) {
      hash_mips = XXH3_64bits(mem.TranslatePhysical(uint32_t(key.mip_page) << 12),
                              texture.GetGuestMipsSize());
      __atomic_fetch_add(&tt_tex_stats[3], uint64_t(texture.GetGuestMipsSize()), __ATOMIC_RELAXED);
    }
    auto it = states.find(&texture);
    const bool known = it != states.end() && std::memcmp(&it->second.key, &key, sizeof(key)) == 0;
    if (!gpu_written && known &&
        (!load_base || (it->second.has_base && it->second.base == hash_base)) &&
        (!load_mips || (it->second.has_mips && it->second.mips == hash_mips))) {
      __atomic_fetch_add(&tt_tex_stats[1], 1, __ATOMIC_RELAXED);
      return true;  // same bytes as the last load: the GPU copy is still right
    }
    State& state = states[&texture];
    if (!known) state = State{};
    state.key = key;
    if (load_base) { state.base = hash_base; state.has_base = !gpu_written; }
    if (load_mips) { state.mips = hash_mips; state.has_mips = !gpu_written; }
  }
  VulkanTexture& vulkan_texture = static_cast<VulkanTexture&>(texture);
  TextureKey texture_key = vulkan_texture.key();
"""),
    prelude="// tt-android F9 / D2 / D3\n"
            "extern std::mutex tt_tex_gpu_mutex;\n"
            "extern std::unordered_set<const void*> tt_tex_gpu_dirty;\n"
            "extern \"C\" uint64_t tt_work_stats[16];\n"
            "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_tex_stats[4] = {};\n",
    includes=("#include <cstring>", "#include <mutex>", "#include <unordered_map>",
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

print("\n".join(done))
