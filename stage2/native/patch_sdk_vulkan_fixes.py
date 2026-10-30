#!/usr/bin/env python3
"""Apply known upstream-reported ReXGlue v0.10.0 Vulkan fixes to the SDK tree.

1. rexglue-sdk issue #456: the SPIR-V translator applied the texture-fetch result
   exponent bias (exp_adjust) from fetch-constant word 4 (the LOD-bias bits) instead of
   word 3. Textures with a LOD bias come out darkened, often near black. The D3D12
   (DXBC) translator already reads word 3.
2. rexglue-sdk issue #507: the Vulkan presenter's guest-output paint pipeline is
   recreated (and the previous paint awaited) every frame because swapchain_format is
   never stored.
Idempotent: safe to run more than once.
"""
import sys
from pathlib import Path

sdk = Path(sys.argv[1])
changed = []

# ---- 1. exp_adjust from word 3
p = sdk / "src/graphics/pipeline/shader/spirv_translator_fetch.cpp"
s = p.read_text()
old = """        // Apply the exponent bias from the bits 13:18 of the fetch constant
        // word 4.
        spv::Id result_exponent_bias = builder_->createBinBuiltinCall(
            type_float_, ext_inst_glsl_std_450_, GLSLstd450Ldexp, const_float_1_,
            builder_->createTriOp(spv::OpBitFieldSExtract, type_int_, fetch_constant_word_4_signed,
                                  builder_->makeUintConstant(13), builder_->makeUintConstant(6)));"""
new = """        // Apply the exponent bias from the bits 13:18 of the fetch constant
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
                                  builder_->makeUintConstant(13), builder_->makeUintConstant(6)));"""
if "rexglue-sdk #456" not in s:
    if s.count(old) != 1:
        sys.exit("exp_adjust patch point not found; SDK changed")
    p.write_text(s.replace(old, new))
    changed.append("SPIR-V exp_adjust now reads fetch constant word 3 (#456)")

# ---- 2. presenter pipeline churn
p = sdk / "src/ui/vulkan/vulkan_presenter.cpp"
s = p.read_text()
if "rexglue-sdk #507" not in s:
    import re
    # find the assignment of the newly created swapchain pipeline and store the format after it
    m = re.search(r"(\n(\s*)(\w[\w\.\[\]>-]*\.swapchain_pipeline)\s*=\s*CreateGuestOutputPaintPipeline\([^;]*\);\n)", s)
    if not m:
        print("NOTE: #507 patch point not found; skipping presenter fix")
    else:
        indent, lhs = m.group(2), m.group(3)
        owner = lhs.rsplit(".swapchain_pipeline", 1)[0]
        add = (f"{indent}if ({lhs} != VK_NULL_HANDLE) {{  // tt-android: rexglue-sdk #507\n"
               f"{indent}  {owner}.swapchain_format = paint_context_.swapchain_render_pass_format;\n"
               f"{indent}}}\n")
        s = s[:m.end()] + add + s[m.end():]
        p.write_text(s)
        changed.append("Vulkan presenter stores swapchain_format (#507)")

# ---- 3. rexglue-sdk #441: skip the /proc/self/maps recheck on every write-watch fault
p = sdk / "src/system/mmio_handler.cpp"
s = p.read_text()
old = """    auto lock = global_critical_region_.Acquire();
    memory::PageAccess cur_access;
    size_t page_length = memory::page_size();
    memory::QueryProtect(fault_host_address, page_length, cur_access);
    if (cur_access != memory::PageAccess::kNoAccess &&
        (!is_write || cur_access != memory::PageAccess::kReadOnly)) {
      // Another thread has cleared this watch. Abort.
      return true;
    }"""
new = """    auto lock = global_critical_region_.Acquire();
    // tt-android: rexglue-sdk #441. On Linux/Android QueryProtect parses all of
    // /proc/self/maps under the global lock on every write-watch fault. The access
    // violation callback below re-checks the authoritative write-watch state, so skip
    // the recheck unless TT_SLOW_WATCH is set.
    static const bool slow_watch_recheck = std::getenv("TT_SLOW_WATCH") != nullptr;
    if (slow_watch_recheck) {
      memory::PageAccess cur_access;
      size_t page_length = memory::page_size();
      memory::QueryProtect(fault_host_address, page_length, cur_access);
      if (cur_access != memory::PageAccess::kNoAccess &&
          (!is_write || cur_access != memory::PageAccess::kReadOnly)) {
        // Another thread has cleared this watch. Abort.
        return true;
      }
    }"""
if "rexglue-sdk #441" not in s:
    if s.count(old) != 1:
        sys.exit("#441 patch point not found; SDK changed")
    s = s.replace(old, new)
    if "#include <cstdlib>" not in s:
        s = s.replace("#include", "#include <cstdlib>\n#include", 1)
    p.write_text(s)
    changed.append("write-watch faults no longer parse /proc/self/maps (#441)")

# ---- 4. tt-android stats: count memory faults (MMIO vs write-watch) and time spent
p = sdk / "src/system/mmio_handler.cpp"
s = p.read_text()
if "tt_mmio_stats" not in s:
    anchors = [
        ("  void* fault_host_address = reinterpret_cast<void*>(ex->fault_address());\n",
         "  void* fault_host_address = reinterpret_cast<void*>(ex->fault_address());\n"
         "  __atomic_fetch_add(&tt_mmio_stats[0], 1, __ATOMIC_RELAXED);  // tt-android stats\n"),
        ("  if (!range) {\n    // Recheck",
         "  if (!range) {\n"
         "    __atomic_fetch_add(&tt_mmio_stats[2], 1, __ATOMIC_RELAXED);  // tt-android stats\n"
         "    const auto tt_watch_t0 = std::chrono::steady_clock::now();\n"
         "    // Recheck"),
        ("      return access_violation_callback_(std::move(lock), access_violation_callback_context_,\n"
         "                                        fault_host_address, is_write);\n",
         "      const bool tt_handled = access_violation_callback_(\n"
         "          std::move(lock), access_violation_callback_context_, fault_host_address, is_write);\n"
         "      __atomic_fetch_add(&tt_mmio_stats[3],\n"
         "                         uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
         "                                      std::chrono::steady_clock::now() - tt_watch_t0)\n"
         "                                      .count()),\n"
         "                         __ATOMIC_RELAXED);\n"
         "      return tt_handled;\n"),
        ("  auto rip = ex->pc();\n",
         "  __atomic_fetch_add(&tt_mmio_stats[1], 1, __ATOMIC_RELAXED);  // tt-android stats\n"
         "  auto rip = ex->pc();\n"),
    ]
    for old, new in anchors:
        if s.count(old) != 1:
            sys.exit("mmio stats patch point not found: " + old.strip()[:60])
        s = s.replace(old, new)
    decl = ("\n// tt-android: fault counters read by the in-game profiler (dlsym).\n"
            "// [0] faults in guest memory, [1] MMIO, [2] write-watch, [3] write-watch ns\n"
            "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_mmio_stats[8] = {};\n")
    ns = s.find("\nnamespace")  # first namespace: declare at global scope
    if ns < 0:
        sys.exit("mmio stats: namespace anchor not found")
    s = s[:ns] + decl + s[ns:]
    s = s.replace("#include", "#include <chrono>\n#include <cstdint>\n#include", 1)
    p.write_text(s)
    changed.append("memory-fault counters for the profiler")

# ---- 5. tt-android stats: audio worker callback rate and cost, SDL silence fills
p = sdk / "src/audio/audio_system.cpp"
s = p.read_text()
if "tt_audio_stats" not in s:
    old = """        function_dispatcher_->Execute(worker_thread_->thread_state(), client_callback, args,
                                      rex::countof(args));
"""
    new = """        const auto tt_audio_t0 = std::chrono::steady_clock::now();  // tt-android stats
        function_dispatcher_->Execute(worker_thread_->thread_state(), client_callback, args,
                                      rex::countof(args));
        __atomic_fetch_add(&tt_audio_stats[0], 1, __ATOMIC_RELAXED);
        __atomic_fetch_add(&tt_audio_stats[1],
                           uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                        std::chrono::steady_clock::now() - tt_audio_t0)
                                        .count()),
                           __ATOMIC_RELAXED);
"""
    old2 = """    if (result.first == rex::thread::WaitResult::kTimeout) {
"""
    new2 = """    if (result.first == rex::thread::WaitResult::kTimeout) {
      __atomic_fetch_add(&tt_audio_stats[3], 1, __ATOMIC_RELAXED);  // tt-android stats
"""
    for o, n in ((old, new), (old2, new2)):
        if s.count(o) != 1:
            sys.exit("audio stats patch point not found: " + o.strip()[:60])
        s = s.replace(o, n)
    ns = s.find("\nnamespace")  # first namespace: declare at global scope
    if ns < 0:
        sys.exit("audio stats: namespace anchor not found")
    s = (s[:ns] + "\n// tt-android: [0] callbacks, [1] callback ns, [2] SDL silence fills, [3] wait timeouts\n"
         "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_audio_stats[8] = {};\n" + s[ns:])
    s = s.replace("#include", "#include <chrono>\n#include <cstdint>\n#include", 1)
    p.write_text(s)
    p2 = sdk / "src/audio/sdl/sdl_audio_driver.cpp"
    t = p2.read_text()
    o = """    if (driver->frames_queued_.empty()) {
"""
    if t.count(o) != 1:
        sys.exit("audio silence patch point not found")
    t = t.replace(o, o + "      __atomic_fetch_add(&tt_audio_stats[2], 1, __ATOMIC_RELAXED);  // tt-android stats\n")
    t = t.replace("#include", "#include <cstdint>\nextern \"C\" uint64_t tt_audio_stats[8];\n#include", 1)
    p2.write_text(t)
    changed.append("audio worker counters for the profiler")

# ---- 6. Multi-object waits on Linux/Android busy-spun: the 1 ms poll sleep was computed in
#         whole milliseconds, so the last (sub-)millisecond of every slice became sleep_for(0).
#         The audio worker waits this way (with alertable 1 ms slices) and burned a full core.
p = sdk / "src/core/threading_posix.cpp"
s = p.read_text()
if "tt-android: no busy spin" not in s:
    old = """      if (timeout == std::chrono::milliseconds::max()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      } else {
        auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - now);
        auto sleep_time = std::min(remaining, std::chrono::milliseconds(1));
        std::this_thread::sleep_for(sleep_time);
      }"""
    new = """      // tt-android: no busy spin. Sleep in microseconds; the old millisecond cast
      // rounded the final slice down to sleep_for(0) and spun.
      constexpr auto kPollSlice = std::chrono::microseconds(500);
      if (timeout == std::chrono::milliseconds::max()) {
        std::this_thread::sleep_for(kPollSlice);
      } else {
        auto remaining = std::chrono::duration_cast<std::chrono::microseconds>(end_time - now);
        if (remaining < std::chrono::microseconds(50)) {
          return std::make_pair<WaitResult, size_t>(WaitResult::kTimeout, 0);
        }
        std::this_thread::sleep_for(std::min(remaining, kPollSlice));
      }"""
    if s.count(old) != 1:
        sys.exit("WaitMultiple poll patch point not found")
    s = s.replace(old, new)
    old2 = """      if (!all_locked) {
        locks.clear();
        std::this_thread::yield();
        continue;
      }"""
    new2 = """      if (!all_locked) {
        locks.clear();
        std::this_thread::sleep_for(std::chrono::microseconds(20));  // tt-android: was yield()
        continue;
      }"""
    if s.count(old2) == 1:
        s = s.replace(old2, new2)
    p.write_text(s)
    changed.append("multi-object waits sleep instead of spinning (audio worker)")

# ---- 7. GPU sync counters: how often and how long the command processor stalls on the GPU
p = sdk / "src/graphics/vulkan/command_processor.cpp"
s = p.read_text()
if "tt_gpu_stats" not in s:
    import re
    sites = {"EndGuestOcclusionQuery": 0, "IssueDraw": 1, "IssueCopy": 2}
    out, pos, count = [], 0, 0
    func_re = re.compile(r"^[A-Za-z].*VulkanCommandProcessor::(\w+)\(", re.M)
    for m in re.finditer(r"AwaitAllQueueOperationsCompletion\(\)", s):
        before = list(func_re.finditer(s, 0, m.start()))
        name = before[-1].group(1) if before else ""
        if name == "ShutdownContext":
            continue
        site = next((v for k, v in sites.items() if name.startswith(k)), 3)
        out.append(s[pos:m.start()])
        out.append(f"TT_AWAIT_ALL({site})")
        print(f"  wait site {site}: {name}")
        pos = m.end()
        count += 1
    out.append(s[pos:])
    s = "".join(out)
    old = "  CheckSubmissionFenceAndDeviceLoss(await_submission);\n  if (device_lost_ || submission_completed_ < await_submission) {"
    if s.count(old) != 1:
        sys.exit("BeginSubmission patch point not found")
    s = s.replace(old, "  {\n    const auto tt_t0 = std::chrono::steady_clock::now();\n"
                       "    CheckSubmissionFenceAndDeviceLoss(await_submission);\n"
                       "    TtGpuRecord(4, tt_t0);  // frames-in-flight throttle\n  }\n"
                       "  if (device_lost_ || submission_completed_ < await_submission) {")
    old = "  uint32_t host_index = active_occlusion_query_.host_index;\n  active_occlusion_query_ = {};\n"
    if s.count(old) == 1:
        s = s.replace(old, old + "  __atomic_fetch_add(&tt_gpu_stats[15], 1, __ATOMIC_RELAXED);  // tt-android\n")
    decl = """
// tt-android: GPU stall counters read by the in-game profiler (dlsym).
// [0..4] calls: occlusion query, memexport, resolve copy, other, frame throttle
// [8..12] nanoseconds spent in each; [15] occlusion queries ended
extern "C" __attribute__((visibility("default"))) uint64_t tt_gpu_stats[16] = {};
static inline void TtGpuRecord(int site, std::chrono::steady_clock::time_point t0) {
  __atomic_fetch_add(&tt_gpu_stats[site], 1, __ATOMIC_RELAXED);
  __atomic_fetch_add(&tt_gpu_stats[8 + site],
                     uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                                  std::chrono::steady_clock::now() - t0)
                                  .count()),
                     __ATOMIC_RELAXED);
}
#define TT_AWAIT_ALL(site)                                          \\
  ([&]() {                                                          \\
    const auto tt_t0 = std::chrono::steady_clock::now();            \\
    const bool tt_r = AwaitAllQueueOperationsCompletion();          \\
    TtGpuRecord(site, tt_t0);                                       \\
    return tt_r;                                                    \\
  }())
"""
    ns = s.find("\nnamespace")
    if ns < 0:
        sys.exit("gpu stats: namespace anchor not found")
    s = s[:ns] + decl + s[ns:]
    s = s.replace("#include", "#include <chrono>\n#include <cstdint>\n#include", 1)
    p.write_text(s)
    changed.append(f"GPU stall counters ({count} wait sites)")

# ---- 8. Frame pacing counters: guest vblanks, command-processor swaps, WAIT_REG_MEM sleeps
p = sdk / "src/graphics/graphics_system.cpp"
s = p.read_text()
if "tt_frame_stats" not in s:
    old = "void GraphicsSystem::MarkVblank() {\n"
    if s.count(old) != 1:
        sys.exit("MarkVblank patch point not found")
    s = s.replace(old, old + "  __atomic_fetch_add(&tt_frame_stats[0], 1, __ATOMIC_RELAXED);  // tt-android\n")
    ns = s.find("\nnamespace")
    s = (s[:ns] + "\n// tt-android: [0] guest vblanks, [1] CP wait-for-memory sleeps, [2] their ns,\n"
         "// [3] command-processor swaps. Read by the in-game profiler (dlsym).\n"
         "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_frame_stats[8] = {};\n" + s[ns:])
    s = s.replace("#include", "#include <cstdint>\n#include", 1)
    p.write_text(s)
    p2 = sdk / "src/graphics/command_processor.cpp"
    t = p2.read_text()
    o1 = "          rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));\n"
    n1 = ("          const auto tt_t0 = std::chrono::steady_clock::now();  // tt-android\n"
          "          rex::thread::Sleep(std::chrono::milliseconds(wait / 0x100));\n"
          "          __atomic_fetch_add(&tt_frame_stats[1], 1, __ATOMIC_RELAXED);\n"
          "          __atomic_fetch_add(&tt_frame_stats[2],\n"
          "                             uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
          "                                          std::chrono::steady_clock::now() - tt_t0)\n"
          "                                          .count()),\n"
          "                             __ATOMIC_RELAXED);\n")
    o2 = "  ++counter_;\n  return true;\n"
    for o, n in ((o1, n1), (o2, "  ++counter_;\n  __atomic_fetch_add(&tt_frame_stats[3], 1, __ATOMIC_RELAXED);  // tt-android\n  return true;\n")):
        if t.count(o) != 1:
            sys.exit("frame stats patch point not found: " + o.strip()[:50])
        t = t.replace(o, n)
    t = t.replace("#include", "#include <chrono>\n#include <cstdint>\nextern \"C\" uint64_t tt_frame_stats[8];\n#include", 1)
    p2.write_text(t)
    changed.append("frame pacing counters (vblanks, swaps, CP waits)")

# ---- 9. Frames in flight: room for up to 6, depth chosen at run time (TT_FRAMES_IN_FLIGHT,
#         default 3 = upstream). The game closes ~3 host frames per guest frame, so with 3
#         in flight the CPU waits for the GPU to finish the previous guest frame entirely.
p = sdk / "include/rex/graphics/vulkan/command_processor.h"
s = p.read_text()
if "tt-android: frames in flight" not in s:
    old = "  static constexpr uint32_t kMaxFramesInFlight = 3;"
    if s.count(old) != 1:
        sys.exit("kMaxFramesInFlight not found")
    s = s.replace(old, "  static constexpr uint32_t kMaxFramesInFlight = 6;  // tt-android: frames in flight (was 3)")
    p.write_text(s)
    p2 = sdk / "src/graphics/vulkan/command_processor.cpp"
    t = p2.read_text()
    old = "      is_opening_frame ? closed_frame_submissions_[frame_current_ % kMaxFramesInFlight] : 0;"
    new = ("      is_opening_frame && frame_current_ >= TtFramesInFlight()\n"
           "          ? closed_frame_submissions_[(frame_current_ - TtFramesInFlight()) % kMaxFramesInFlight]\n"
           "          : 0;")
    if t.count(old) != 1:
        sys.exit("frame throttle index not found")
    t = t.replace(old, new)
    helper = ("\n// tt-android: how many host frames may be queued on the GPU (TT_FRAMES_IN_FLIGHT, 1..6).\n"
              "static uint64_t TtFramesInFlight() {\n"
              "  static const uint64_t depth = [] {\n"
              "    const char* v = std::getenv(\"TT_FRAMES_IN_FLIGHT\");\n"
              "    long n = v ? std::strtol(v, nullptr, 10) : 3;\n"
              "    return uint64_t(n < 1 ? 1 : n > 6 ? 6 : n);\n"
              "  }();\n"
              "  return depth;\n"
              "}\n")
    ns = t.find("\nnamespace")
    t = t[:ns] + helper + t[ns:]
    t = t.replace("#include", "#include <cstdlib>\n#include", 1)
    p2.write_text(t)
    changed.append("frames in flight adjustable (TT_FRAMES_IN_FLIGHT)")

# ---- 10. GPU work counters: draws, resolves, texture loads, render-target transfers, uploads
vk = sdk / "src/graphics/vulkan"
if "tt_work_stats" not in (vk / "command_processor.cpp").read_text():
    edits = [
        ("command_processor.cpp",
         "bool VulkanCommandProcessor::IssueDraw(xenos::PrimitiveType prim_type, uint32_t index_count,\n"
         "                                       IndexBufferInfo* index_buffer_info,\n"
         "                                       bool major_mode_explicit) {\n",
         "  __atomic_fetch_add(&tt_work_stats[0], 1, __ATOMIC_RELAXED);  // tt-android\n"),
        ("command_processor.cpp", "bool VulkanCommandProcessor::IssueCopy() {\n",
         "  __atomic_fetch_add(&tt_work_stats[1], 1, __ATOMIC_RELAXED);  // tt-android\n"),
        ("texture_cache.cpp",
         "bool VulkanTextureCache::LoadTextureDataFromResidentMemoryImpl(Texture& texture, bool load_base,\n"
         "                                                               bool load_mips) {\n",
         "  __atomic_fetch_add(&tt_work_stats[2], 1, __ATOMIC_RELAXED);  // tt-android\n"
         "  __atomic_fetch_add(&tt_work_stats[3], uint64_t(texture.GetGuestBaseSize()) +\n"
         "                     (load_mips ? uint64_t(texture.GetGuestMipsSize()) : 0), __ATOMIC_RELAXED);\n"),
        ("render_target_cache.cpp",
         "    const Transfer::Rectangle* resolve_clear_rectangle) {\n  assert_true(GetPath() == Path::kHostRenderTargets);\n",
         "  __atomic_fetch_add(&tt_work_stats[4], 1, __ATOMIC_RELAXED);  // tt-android\n"
         "  if (render_target_transfers) {\n"
         "    for (uint32_t tt_i = 0; tt_i < render_target_count; ++tt_i) {\n"
         "      __atomic_fetch_add(&tt_work_stats[5], uint64_t(render_target_transfers[tt_i].size()),\n"
         "                         __ATOMIC_RELAXED);\n"
         "    }\n"
         "  }\n"),
        ("shared_memory.cpp",
         "    const std::vector<std::pair<uint32_t, uint32_t>>& upload_page_ranges) {\n",
         "  __atomic_fetch_add(&tt_work_stats[6], 1, __ATOMIC_RELAXED);  // tt-android\n"
         "  for (const auto& tt_range : upload_page_ranges) {\n"
         "    __atomic_fetch_add(&tt_work_stats[7], uint64_t(tt_range.second) << page_size_log2(),\n"
         "                       __ATOMIC_RELAXED);\n"
         "  }\n"),
    ]
    for fname, anchor, ins in edits:
        f = vk / fname
        t = f.read_text()
        if t.count(anchor) != 1:
            sys.exit(f"work stats patch point not found in {fname}: {anchor.strip()[:60]}")
        t = t.replace(anchor, anchor + ins)
        if fname == "command_processor.cpp" and "uint64_t tt_work_stats[16] = {}" not in t:
            ns = t.find("\nnamespace")
            t = (t[:ns] + "\n// tt-android: [0] draws [1] resolves [2] texture loads [3] texture bytes\n"
                 "// [4] RT transfer passes [5] RT transfers [6] upload calls [7] upload bytes\n"
                 "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_work_stats[16] = {};\n" + t[ns:])
        elif fname != "command_processor.cpp" and "extern \"C\" uint64_t tt_work_stats" not in t:
            t = t.replace("#include", "#include <cstdint>\nextern \"C\" uint64_t tt_work_stats[16];\n#include", 1)
        f.write_text(t)
    changed.append("GPU work counters (draws, resolves, textures, transfers, uploads)")

# ---- 12. GPU timestamps by kind of work (render passes, compute dispatches, copies).
#          Only active when the profiler runs (TT_PROFILE set).
dcb = sdk / "src/graphics/vulkan/deferred_command_buffer.cpp"
t = dcb.read_text()
if "tt_gputime_stats" not in t:
    timing = """
// ---- tt-android: GPU timestamps by kind of work, read by the in-game profiler (dlsym).
// [0] other ns  [1] full-screen render passes ns  [2] smaller render passes ns
// [3] compute dispatches ns  [4] copies ns  [8] full-screen passes  [9] smaller passes
// [10] dispatches  [11] copies  [12] submissions timed
extern "C" __attribute__((visibility("default"))) uint64_t tt_gputime_stats[16] = {};
namespace tt_timing {
constexpr uint32_t kSlots = 8, kPerSlot = 1024;
struct Slot {
  uint64_t submission = 0;
  uint32_t count = 0;
  bool pending = false;
  uint8_t cat[kPerSlot];
};
bool enabled = std::getenv("TT_PROFILE") != nullptr;
bool initialized = false;
VkQueryPool pool = VK_NULL_HANDLE;
PFN_vkCmdWriteTimestamp write_timestamp = nullptr;
PFN_vkGetQueryPoolResults get_results = nullptr;
double period_ns = 1.0;
Slot slots[kSlots];
uint32_t current = UINT32_MAX;
VkCommandBuffer cb = VK_NULL_HANDLE;
uint8_t pass_cat = 2;

void Mark(uint8_t cat) {
  if (current == UINT32_MAX) return;
  Slot& s = slots[current];
  if (s.count >= kPerSlot) return;
  write_timestamp(cb, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, pool, current * kPerSlot + s.count);
  s.cat[s.count++] = cat;
}

bool Init(const VulkanCommandProcessor& cp) {
  initialized = true;
  const ui::vulkan::VulkanDevice* device = cp.GetVulkanDevice();
  const auto& ifn = device->vulkan_instance()->functions();
  const auto& dfn = device->functions();
  write_timestamp = reinterpret_cast<PFN_vkCmdWriteTimestamp>(
      ifn.vkGetDeviceProcAddr(device->device(), "vkCmdWriteTimestamp"));
  get_results = reinterpret_cast<PFN_vkGetQueryPoolResults>(
      ifn.vkGetDeviceProcAddr(device->device(), "vkGetQueryPoolResults"));
  if (!write_timestamp || !get_results) return enabled = false;
  VkPhysicalDeviceProperties props;
  ifn.vkGetPhysicalDeviceProperties(device->physical_device(), &props);
  period_ns = props.limits.timestampPeriod > 0 ? props.limits.timestampPeriod : 1.0;
  VkQueryPoolCreateInfo info = {};
  info.sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;
  info.queryType = VK_QUERY_TYPE_TIMESTAMP;
  info.queryCount = kSlots * kPerSlot;
  if (dfn.vkCreateQueryPool(device->device(), &info, nullptr, &pool) != VK_SUCCESS) {
    return enabled = false;
  }
  return true;
}
}  // namespace tt_timing

void TtGpuTimingBegin(const VulkanCommandProcessor& cp, VkCommandBuffer command_buffer,
                      uint64_t submission) {
  using namespace tt_timing;
  current = UINT32_MAX;
  if (!enabled || (!initialized && !Init(cp))) return;
  const uint32_t index = uint32_t(submission % kSlots);
  Slot& s = slots[index];
  if (s.pending) return;  // results not collected yet; skip timing this one
  cp.GetVulkanDevice()->functions().vkCmdResetQueryPool(command_buffer, pool, index * kPerSlot,
                                                        kPerSlot);
  s.submission = submission;
  s.count = 0;
  s.pending = true;
  current = index;
  cb = command_buffer;
  Mark(0);
}

void TtGpuTimingEnd() {
  tt_timing::Mark(0);
  tt_timing::current = UINT32_MAX;
}

void TtGpuTimingCollect(const VulkanCommandProcessor& cp, uint64_t completed) {
  using namespace tt_timing;
  if (!enabled || !initialized) return;
  static uint64_t results[kPerSlot];
  for (uint32_t i = 0; i < kSlots; ++i) {
    Slot& s = slots[i];
    if (!s.pending || s.submission > completed || i == current) continue;
    s.pending = false;
    if (s.count < 2) continue;
    if (get_results(cp.GetVulkanDevice()->device(), pool, i * kPerSlot, s.count,
                    sizeof(uint64_t) * s.count, results, sizeof(uint64_t),
                    VK_QUERY_RESULT_64_BIT) != VK_SUCCESS) {
      continue;
    }
    for (uint32_t k = 1; k < s.count; ++k) {
      if (results[k] < results[k - 1]) continue;
      const uint8_t c = s.cat[k] < 5 ? s.cat[k] : 0;
      __atomic_fetch_add(&tt_gputime_stats[c],
                         uint64_t(double(results[k] - results[k - 1]) * period_ns),
                         __ATOMIC_RELAXED);
    }
    __atomic_fetch_add(&tt_gputime_stats[12], 1, __ATOMIC_RELAXED);
  }
}
"""
    anchor = "namespace rex::graphics::vulkan {\n"
    if t.count(anchor) != 1:
        sys.exit("deferred command buffer namespace not found")
    t = t.replace(anchor, anchor + timing, 1)
    t = t.replace("#include <cstddef>", "#include <cstddef>\n#include <cstdlib>", 1)
    hooks = [
        ("      case Command::kVkBeginRenderPass: {\n"
         "        auto& args = *reinterpret_cast<const ArgsVkBeginRenderPass*>(stream);\n",
         "        tt_timing::Mark(0);  // tt-android\n"
         "        tt_timing::pass_cat = uint64_t(args.render_area.extent.width) *\n"
         "                                  args.render_area.extent.height >= 600000 ? 1 : 2;\n"
         "        __atomic_fetch_add(&tt_gputime_stats[7 + tt_timing::pass_cat], 1, __ATOMIC_RELAXED);\n"),
        ("      case Command::kVkBeginRendering: {\n"
         "        auto& args = *reinterpret_cast<const ArgsVkBeginRendering*>(stream);\n",
         "        tt_timing::Mark(0);  // tt-android\n"
         "        tt_timing::pass_cat = uint64_t(args.render_area.extent.width) *\n"
         "                                  args.render_area.extent.height >= 600000 ? 1 : 2;\n"
         "        __atomic_fetch_add(&tt_gputime_stats[7 + tt_timing::pass_cat], 1, __ATOMIC_RELAXED);\n"),
    ]
    for anchor, ins in hooks:
        if t.count(anchor) != 1:
            sys.exit("timing hook point not found: " + anchor.strip()[:50])
        t = t.replace(anchor, anchor + ins)
    simple = [
        ("        dfn.vkCmdEndRenderPass(command_buffer);\n", None, "tt_timing::Mark(tt_timing::pass_cat);"),
        ("        dfn.vkCmdEndRendering(command_buffer);\n", None, "tt_timing::Mark(tt_timing::pass_cat);"),
    ]
    for line, _, after in simple:
        if t.count(line) != 1:
            sys.exit("timing end hook not found: " + line.strip())
        t = t.replace(line, line + "        " + after + "  // tt-android\n")
    wraps = [
        ("        dfn.vkCmdDispatch(command_buffer,", 3, 10),
        ("        dfn.vkCmdCopyBuffer(command_buffer,", 4, 11),
        ("        dfn.vkCmdCopyBufferToImage(", 4, 11),
        ("        dfn.vkCmdClearColorImage(", 4, 11),
    ]
    for start, cat, counter in wraps:
        i = t.find(start)
        if i < 0 or t.count(start) != 1:
            sys.exit("timing wrap point not found: " + start.strip())
        end = t.find(";\n", i) + 2
        t = (t[:i] + "        tt_timing::Mark(0);  // tt-android\n" + t[i:end] +
             f"        tt_timing::Mark({cat});\n"
             f"        __atomic_fetch_add(&tt_gputime_stats[{counter}], 1, __ATOMIC_RELAXED);\n" + t[end:])
    dcb.write_text(t)
    cp = sdk / "src/graphics/vulkan/command_processor.cpp"
    c = cp.read_text()
    old = "    deferred_command_buffer_.Execute(command_buffer.buffer);\n"
    if c.count(old) != 1:
        sys.exit("Execute call not found")
    c = c.replace(old, "    TtGpuTimingBegin(*this, command_buffer.buffer, GetCurrentSubmission());  // tt-android\n"
                       + old + "    TtGpuTimingEnd();\n")
    old = "    TtGpuRecord(4, tt_t0);  // frames-in-flight throttle\n"
    if c.count(old) != 1:
        sys.exit("collect point not found (needs patch 7)")
    c = c.replace(old, old + "    TtGpuTimingCollect(*this, submission_completed_);  // tt-android\n")
    decl = ("namespace rex::graphics::vulkan {\n"
            "void TtGpuTimingBegin(const VulkanCommandProcessor& cp, VkCommandBuffer command_buffer,\n"
            "                      uint64_t submission);\n"
            "void TtGpuTimingEnd();\n"
            "void TtGpuTimingCollect(const VulkanCommandProcessor& cp, uint64_t completed);\n"
            "}  // namespace rex::graphics::vulkan\n")
    ns = c.find("\nnamespace rex::graphics::vulkan {")
    if ns < 0:
        sys.exit("vulkan namespace not found in command_processor.cpp")
    c = c[:ns] + "\n" + decl + c[ns:]
    cp.write_text(c)
    changed.append("GPU timestamps by kind of work")

# ---- 13. Presenter: wait for the swapchain image on the CPU (fence) instead of making the
#          shared GPU queue wait on the acquire semaphore. On Android the acquire only
#          completes when the display releases a buffer (at vsync), and every emulator
#          submission queued behind the paint stalled with it: each host frame cost a
#          full refresh (20 FPS with 3 per guest frame, 30 with 2). TT_PRESENT_FENCE=off
#          restores upstream behaviour.
p = sdk / "src/ui/vulkan/vulkan_presenter.cpp"
s = p.read_text()
if "tt-android: acquire fence" not in s:
    old = """  VkSemaphore acquire_semaphore = paint_submission.acquire_semaphore();
  uint32_t swapchain_image_index;
  VkResult acquire_result =
      dfn.vkAcquireNextImageKHR(device, paint_context_.swapchain, UINT64_MAX, acquire_semaphore,
                                VK_NULL_HANDLE, &swapchain_image_index);
"""
    new = """  VkSemaphore acquire_semaphore = paint_submission.acquire_semaphore();
  uint32_t swapchain_image_index;
  // tt-android: acquire fence (see patch notes). One fence per painting thread.
  static const bool tt_fence_acquire = [] {
    const char* v = std::getenv("TT_PRESENT_FENCE");
    return !(v && std::string_view(v) == "off");
  }();
  thread_local VkFence tt_acquire_fence = VK_NULL_HANDLE;
  bool tt_acquired_with_fence = false;
  if (tt_fence_acquire && tt_acquire_fence == VK_NULL_HANDLE) {
    VkFenceCreateInfo tt_fence_info = {};
    tt_fence_info.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    if (dfn.vkCreateFence(device, &tt_fence_info, nullptr, &tt_acquire_fence) != VK_SUCCESS) {
      tt_acquire_fence = VK_NULL_HANDLE;
    }
  }
  VkResult acquire_result;
  if (tt_fence_acquire && tt_acquire_fence != VK_NULL_HANDLE) {
    acquire_result = dfn.vkAcquireNextImageKHR(device, paint_context_.swapchain, UINT64_MAX,
                                               VK_NULL_HANDLE, tt_acquire_fence,
                                               &swapchain_image_index);
    if (acquire_result == VK_SUCCESS || acquire_result == VK_SUBOPTIMAL_KHR) {
      dfn.vkWaitForFences(device, 1, &tt_acquire_fence, VK_TRUE, UINT64_MAX);
      dfn.vkResetFences(device, 1, &tt_acquire_fence);
      tt_acquired_with_fence = true;
    }
  } else {
    acquire_result =
        dfn.vkAcquireNextImageKHR(device, paint_context_.swapchain, UINT64_MAX, acquire_semaphore,
                                  VK_NULL_HANDLE, &swapchain_image_index);
  }
"""
    if s.count(old) != 1:
        sys.exit("presenter acquire patch point not found")
    s = s.replace(old, new)
    old2 = """  submit_info.waitSemaphoreCount = 1;
  submit_info.pWaitSemaphores = &acquire_semaphore;
  submit_info.pWaitDstStageMask = &acquire_semaphore_wait_stage;
"""
    new2 = """  submit_info.waitSemaphoreCount = tt_acquired_with_fence ? 0 : 1;  // tt-android
  submit_info.pWaitSemaphores = tt_acquired_with_fence ? nullptr : &acquire_semaphore;
  submit_info.pWaitDstStageMask = tt_acquired_with_fence ? nullptr : &acquire_semaphore_wait_stage;
"""
    if s.count(old2) != 1:
        sys.exit("presenter submit patch point not found")
    s = s.replace(old2, new2)
    for inc in ("#include <cstdlib>", "#include <string_view>"):
        if inc not in s:
            s = s.replace("#include", inc + "\n#include", 1)
    p.write_text(s)
    changed.append("presenter waits for swapchain images on the CPU (no GPU queue stall)")

# ---- 14. Latency probes on top of 12: how long a submission waits in the queue before
#          the GPU starts it, and how long after the GPU finishes a frame the CPU's fence
#          wait actually returns. GPU and CPU clocks are aligned by the smallest observed
#          (GPU start - CPU submit) offset.
dcb = sdk / "src/graphics/vulkan/deferred_command_buffer.cpp"
t = dcb.read_text()
if "tt-android: latency probes" not in t:
    reps = [
        ("  uint32_t count = 0;\n  bool pending = false;\n",
         "  uint32_t count = 0;\n  bool pending = false;\n"
         "  int64_t cpu_submit_ns = 0, gpu_start_ns = 0, gpu_end_ns = 0;  // tt-android: latency probes\n"
         "  bool collected = false;\n"),
        ("uint8_t pass_cat = 2;\n",
         "uint8_t pass_cat = 2;\n"
         "int64_t offset_min = INT64_MAX;\n"
         "int64_t NowNs() {\n"
         "  return std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
         "             std::chrono::steady_clock::now().time_since_epoch()).count();\n"
         "}\n"),
        ("  s.count = 0;\n  s.pending = true;\n",
         "  s.count = 0;\n  s.pending = true;\n  s.collected = false;\n  s.cpu_submit_ns = NowNs();\n"),
        ("    __atomic_fetch_add(&tt_gputime_stats[12], 1, __ATOMIC_RELAXED);\n",
         "    __atomic_fetch_add(&tt_gputime_stats[12], 1, __ATOMIC_RELAXED);\n"
         "    s.gpu_start_ns = int64_t(double(results[0]) * period_ns);\n"
         "    s.gpu_end_ns = int64_t(double(results[s.count - 1]) * period_ns);\n"
         "    s.collected = true;\n"
         "    const int64_t d = s.gpu_start_ns - s.cpu_submit_ns;\n"
         "    if (d < offset_min) offset_min = d;\n"
         "    __atomic_fetch_add(&tt_gputime_stats[13], uint64_t(d - offset_min), __ATOMIC_RELAXED);\n"
         "    __atomic_fetch_add(&tt_gputime_stats[14], 1, __ATOMIC_RELAXED);\n"),
    ]
    for old, new in reps:
        if t.count(old) != 1:
            sys.exit("latency probe patch point not found: " + old.strip()[:50])
        t = t.replace(old, new)
    after_wait = """
// tt-android: called when a frame-throttle fence wait returns; measures how long after the
// GPU finished the awaited submission the CPU noticed.
void TtGpuTimingAfterWait(uint64_t awaited) {
  using namespace tt_timing;
  if (!enabled || !initialized || !awaited || offset_min == INT64_MAX) return;
  for (Slot& s : slots) {
    if (s.collected && s.submission == awaited) {
      const int64_t late = NowNs() - (s.gpu_end_ns - offset_min);
      __atomic_fetch_add(&tt_gputime_stats[5], uint64_t(late > 0 ? late : 0), __ATOMIC_RELAXED);
      __atomic_fetch_add(&tt_gputime_stats[6], 1, __ATOMIC_RELAXED);
      return;
    }
  }
}
"""
    anchor = "void TtGpuTimingCollect("
    t = t.replace(anchor, after_wait.lstrip("\n") + "\n" + anchor, 1)
    if "#include <chrono>" not in t:
        t = t.replace("#include <cstddef>", "#include <chrono>\n#include <cstddef>", 1)
    dcb.write_text(t)
    cp = sdk / "src/graphics/vulkan/command_processor.cpp"
    c = cp.read_text()
    old = "    TtGpuTimingCollect(*this, submission_completed_);  // tt-android\n"
    if c.count(old) != 1:
        sys.exit("latency collect point not found")
    c = c.replace(old, old + "    TtGpuTimingAfterWait(await_submission);\n")
    old = "void TtGpuTimingEnd();\n"
    c = c.replace(old, old + "void TtGpuTimingAfterWait(uint64_t awaited);\n", 1)
    cp.write_text(c)
    changed.append("GPU latency probes (queue delay, fence notify delay)")

# ---- 15. Split the command processor's GPU-side time: CPU replay of the deferred command
#          buffer, vkQueueSubmit, and only the fence waits that actually block.
#          tt_gpu_stats: [5] submits [13] submit ns  [6] blocking waits [14] blocking wait ns
#          [7] deferred replay ns
p = sdk / "src/graphics/vulkan/command_processor.cpp"
c = p.read_text()
if "tt_gpu_stats[13]" not in c:
    reps = [
        ("      submit_result = dfn.vkQueueSubmit(queue_acquisition.queue(), 1, &submit_info, fence);\n",
         "      const auto tt_s0 = std::chrono::steady_clock::now();  // tt-android\n"
         "      submit_result = dfn.vkQueueSubmit(queue_acquisition.queue(), 1, &submit_info, fence);\n"
         "      __atomic_fetch_add(&tt_gpu_stats[5], 1, __ATOMIC_RELAXED);\n"
         "      __atomic_fetch_add(&tt_gpu_stats[13],\n"
         "                         uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
         "                                      std::chrono::steady_clock::now() - tt_s0).count()),\n"
         "                         __ATOMIC_RELAXED);\n"),
        ("    VkResult wait_result =\n"
         "        dfn.vkWaitForFences(device, uint32_t(await_submission - submission_completed_),\n"
         "                            submissions_in_flight_fences_.data(), VK_TRUE, UINT64_MAX);\n",
         "    const auto tt_w0 = std::chrono::steady_clock::now();  // tt-android\n"
         "    VkResult wait_result =\n"
         "        dfn.vkWaitForFences(device, uint32_t(await_submission - submission_completed_),\n"
         "                            submissions_in_flight_fences_.data(), VK_TRUE, UINT64_MAX);\n"
         "    __atomic_fetch_add(&tt_gpu_stats[6], 1, __ATOMIC_RELAXED);\n"
         "    __atomic_fetch_add(&tt_gpu_stats[14],\n"
         "                       uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
         "                                    std::chrono::steady_clock::now() - tt_w0).count()),\n"
         "                       __ATOMIC_RELAXED);\n"),
        ("    deferred_command_buffer_.Execute(command_buffer.buffer);\n",
         "    const auto tt_e0 = std::chrono::steady_clock::now();  // tt-android\n"
         "    deferred_command_buffer_.Execute(command_buffer.buffer);\n"
         "    __atomic_fetch_add(&tt_gpu_stats[7],\n"
         "                       uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
         "                                    std::chrono::steady_clock::now() - tt_e0).count()),\n"
         "                       __ATOMIC_RELAXED);\n"),
    ]
    for old, new in reps:
        if c.count(old) != 1:
            sys.exit("cp split timer patch point not found: " + old.strip()[:60])
        c = c.replace(old, new)
    p.write_text(c)
    changed.append("command-processor submit / wait / replay timers")

# ---- 16. The non-blocking "how far is the GPU" poll used vkWaitForFences with a zero
#          timeout. v27 measured: real blocking waits 0 ms, submits 0.07 ms, yet ~15 ms per
#          call in this function, with the thread mostly inside a driver ioctl. On KGSL a
#          zero-timeout wait isn't instant. vkGetFenceStatus is the plain status query
#          (the presenter's submission tracker already uses it).
p = sdk / "src/graphics/vulkan/command_processor.cpp"
c = p.read_text()
if "tt-android: fence status poll" not in c:
    old = ("    VkResult fence_status =\n"
           "        dfn.vkWaitForFences(device, 1, &submissions_in_flight_fences_[fences_awaited], VK_TRUE, 0);\n")
    new = ("    // tt-android: fence status poll (was vkWaitForFences with timeout 0)\n"
           "    VkResult fence_status =\n"
           "        dfn.vkGetFenceStatus(device, submissions_in_flight_fences_[fences_awaited]);\n")
    if c.count(old) != 1:
        sys.exit("fence poll patch point not found")
    c = c.replace(old, new)
    p.write_text(c)
    changed.append("GPU progress poll uses vkGetFenceStatus (no zero-timeout waits)")

# ---- 17. Time the parts of CheckSubmissionFenceAndDeviceLoss after the waits: the GPU
#          progress poll, the caches' completed-submission updates, and object destruction.
#          tt_fence_stats: [0] poll ns [1] cache updates ns [2] destruction ns [3] calls
p = sdk / "src/graphics/vulkan/command_processor.cpp"
c = p.read_text()
if "tt_fence_stats" not in c:
    def ns_add(slot, var):
        return (f"  __atomic_fetch_add(&tt_fence_stats[{slot}],\n"
                f"                     uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
                f"                                  std::chrono::steady_clock::now() - {var}).count()),\n"
                f"                     __ATOMIC_RELAXED);\n")
    reps = [
        ("  while (fences_awaited < fences_total) {\n",
         "  const auto tt_p0 = std::chrono::steady_clock::now();  // tt-android\n"
         "  __atomic_fetch_add(&tt_fence_stats[3], 1, __ATOMIC_RELAXED);\n"
         "  while (fences_awaited < fences_total) {\n"),
        ("  if (device_lost_) {\n    if (graphics_system_) {\n",
         ns_add(0, "tt_p0") + "  if (device_lost_) {\n    if (graphics_system_) {\n"),
        ("  shared_memory_->CompletedSubmissionUpdated();\n",
         "  const auto tt_c0 = std::chrono::steady_clock::now();  // tt-android\n"
         "  shared_memory_->CompletedSubmissionUpdated();\n"),
        ("  texture_cache_->CompletedSubmissionUpdated(submission_completed_);\n",
         "  texture_cache_->CompletedSubmissionUpdated(submission_completed_);\n"
         + ns_add(1, "tt_c0") +
         "  const auto tt_d0 = std::chrono::steady_clock::now();\n"),
        ("    destroy_memory_.pop_front();\n  }\n}\n",
         "    destroy_memory_.pop_front();\n  }\n" + ns_add(2, "tt_d0") + "}\n"),
    ]
    for old, new in reps:
        if c.count(old) != 1:
            sys.exit("fence stats patch point not found: " + old.strip()[:50])
        c = c.replace(old, new)
    ns = c.find("\nnamespace")
    c = (c[:ns] + "\n// tt-android: [0] GPU progress poll ns [1] cache updates ns [2] destruction ns [3] calls\n"
         "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_fence_stats[16] = {};\n" + c[ns:])
    p.write_text(c)
    changed.append("fence-check segment timers")

# ---- 18. GPU progress from a memory marker instead of fence queries. v29 measured the
#          fence status check itself at ~13 ms per call (26 ms per frame) on KGSL drivers,
#          while the GPU had finished long before. Each submission now ends by writing
#          its number into a small host-visible buffer (after a full barrier); the CPU
#          reads that number to know what's finished, with no driver call. Fences are
#          still signalled by the driver; finished ones are destroyed a few submissions
#          later instead of being reset and reused. TT_FENCE_MARKER=off restores upstream.
p = sdk / "src/graphics/vulkan/command_processor.cpp"
c = p.read_text()
if "tt_marker::" not in c:
    helper = """
// ---- tt-android: GPU progress marker (see patch 18 notes) ----
namespace rex::graphics::vulkan {
namespace tt_marker {
bool enabled = [] {
  const char* v = std::getenv("TT_FENCE_MARKER");
  return !(v && std::string_view(v) == "off");
}();
bool init_failed = false;
VkBuffer buffer = VK_NULL_HANDLE;
VkDeviceMemory memory = VK_NULL_HANDLE;
volatile uint32_t* mapped = nullptr;
PFN_vkCmdFillBuffer fill = nullptr;
std::deque<std::pair<uint64_t, VkFence>> graveyard;

bool Ready(const VulkanCommandProcessor& cp) {
  if (!enabled || init_failed) return false;
  if (mapped) return true;
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
  REXLOG_INFO("tt: GPU progress marker active (fence queries bypassed)");
  return true;
}

// Highest submission the GPU has fully finished, as far as the marker says.
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
    ns = c.find("\nnamespace rex::graphics::vulkan {")
    if ns < 0:
        sys.exit("marker: namespace anchor not found")
    c = c[:ns] + "\n" + helper + c[ns:]
    for inc in ("#include <deque>", "#include <thread>", "#include <cstdlib>", "#include <string_view>"):
        if inc not in c:
            c = c.replace("#include", inc + "\n#include", 1)
    # 1. write the marker at the end of every submission
    old = "    if (dfn.vkEndCommandBuffer(command_buffer.buffer) != VK_SUCCESS) {\n"
    new = ("    if (tt_marker::Ready(*this)) {  // tt-android: GPU progress marker\n"
           "      VkMemoryBarrier tt_before = {};\n"
           "      tt_before.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;\n"
           "      tt_before.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;\n"
           "      tt_before.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
           "      dfn.vkCmdPipelineBarrier(command_buffer.buffer, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,\n"
           "                               VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &tt_before, 0, nullptr,\n"
           "                               0, nullptr);\n"
           "      tt_marker::fill(command_buffer.buffer, tt_marker::buffer, 0, 4,\n"
           "                      uint32_t(GetCurrentSubmission()));\n"
           "      VkMemoryBarrier tt_after = {};\n"
           "      tt_after.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;\n"
           "      tt_after.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;\n"
           "      tt_after.dstAccessMask = VK_ACCESS_HOST_READ_BIT;\n"
           "      dfn.vkCmdPipelineBarrier(command_buffer.buffer, VK_PIPELINE_STAGE_TRANSFER_BIT,\n"
           "                               VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &tt_after, 0, nullptr, 0,\n"
           "                               nullptr);\n"
           "    }\n" + old)
    if c.count(old) != 1:
        sys.exit("marker: vkEndCommandBuffer anchor not found")
    c = c.replace(old, new)
    # 2. use the marker for completion, before the fence wait / poll
    old = "  size_t fences_awaited = 0;\n"
    new = old + ("  if (tt_marker::Ready(*this)) {  // tt-android: GPU progress marker\n"
                 "    uint64_t tt_done = tt_marker::Done(*this, submission_completed_);\n"
                 "    if (await_submission > tt_done) {\n"
                 "      const auto tt_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);\n"
                 "      while (await_submission > tt_done && std::chrono::steady_clock::now() < tt_deadline) {\n"
                 "        std::this_thread::sleep_for(std::chrono::microseconds(50));\n"
                 "        tt_done = tt_marker::Done(*this, submission_completed_);\n"
                 "      }\n"
                 "    }\n"
                 "    if (await_submission <= tt_done) {\n"
                 "      // Marker covers everything needed: skip the driver fence wait and poll.\n"
                 "      fences_awaited = size_t(std::min<uint64_t>(\n"
                 "          fences_total, tt_done > submission_completed_ ? tt_done - submission_completed_ : 0));\n"
                 "      fences_total = fences_awaited;\n"
                 "      await_submission = 0;\n"
                 "    }\n"
                 "  }\n")
    if c.count(old) != 1:
        sys.exit("marker: fences_awaited anchor not found")
    c = c.replace(old, new)
    # 3. don't reset/reuse fences the marker retired; destroy them a few submissions later
    old = ("  fences_free_.insert(fences_free_.cend(), submissions_in_flight_fences_.cbegin(),\n"
           "                      submissions_in_flight_fences_awaited_end);\n")
    new = ("  if (tt_marker::Ready(*this)) {  // tt-android: GPU progress marker\n"
           "    for (auto it = submissions_in_flight_fences_.cbegin(); it != submissions_in_flight_fences_awaited_end;\n"
           "         ++it) {\n"
           "      tt_marker::graveyard.emplace_back(submission_completed_ + fences_awaited, *it);\n"
           "    }\n"
           "    while (!tt_marker::graveyard.empty() &&\n"
           "           tt_marker::graveyard.front().first + 8 <= submission_completed_ + fences_awaited) {\n"
           "      dfn.vkDestroyFence(device, tt_marker::graveyard.front().second, nullptr);\n"
           "      tt_marker::graveyard.pop_front();\n"
           "    }\n"
           "  } else {\n"
           "  " + old.replace("\n                      ", "\n                        ") + "  }\n")
    if c.count(old) != 1:
        sys.exit("marker: fence reclaim anchor not found")
    c = c.replace(old, new)
    p.write_text(c)
    changed.append("GPU progress read from a memory marker (no fence queries)")

# ---- 19. Presenter/guest-output submission tracker: don't query fences that are almost
#          certainly still pending. v30 profile: 65% of the command processor thread was
#          in vkGetFenceStatus via VulkanSubmissionTracker::UpdateAndGetCompletedSubmission
#          (from RefreshGuestOutput on every swap). On KGSL a status query on a fence that
#          hasn't signalled yet sits in the kernel for ~13 ms. Fences younger than
#          TT_FENCE_MIN_AGE_MS (default 100; 0 = upstream) are treated as pending without
#          asking the driver. Real blocking waits are unchanged.
#          tt_tracker_stats: [0] status query ns [1] blocking wait ns [2] queries [3] waits
p = sdk / "src/ui/vulkan/vulkan_submission_tracker.cpp"
t = p.read_text()
if "tt_tracker_stats" not in t:
    helper = """
// tt-android: fence age gate (see patch notes)
extern "C" __attribute__((visibility("default"))) uint64_t tt_tracker_stats[8] = {};
namespace {
std::mutex tt_fence_mutex;
std::unordered_map<VkFence, int64_t> tt_fence_submit_ns;
int64_t TtNowNs() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}
void TtStampFence(VkFence fence) {
  std::lock_guard<std::mutex> lock(tt_fence_mutex);
  tt_fence_submit_ns[fence] = TtNowNs();
}
bool TtFenceTooYoung(VkFence fence) {
  static const int64_t min_age_ns = [] {
    const char* v = std::getenv("TT_FENCE_MIN_AGE_MS");
    return int64_t(v ? std::atoi(v) : 100) * 1000000;
  }();
  if (min_age_ns <= 0) return false;
  std::lock_guard<std::mutex> lock(tt_fence_mutex);
  auto it = tt_fence_submit_ns.find(fence);
  return it != tt_fence_submit_ns.end() && TtNowNs() - it->second < min_age_ns;
}
VkResult TtTimedStatus(const rex::ui::vulkan::VulkanDevice::Functions& dfn, VkDevice device,
                       VkFence fence) {
  const int64_t t0 = TtNowNs();
  const VkResult r = dfn.vkGetFenceStatus(device, fence);
  __atomic_fetch_add(&tt_tracker_stats[0], uint64_t(TtNowNs() - t0), __ATOMIC_RELAXED);
  __atomic_fetch_add(&tt_tracker_stats[2], 1, __ATOMIC_RELAXED);
  return r;
}
}  // namespace
"""
    anchor = "namespace rex {\nnamespace ui {\nnamespace vulkan {\n"
    if t.count(anchor) != 1:
        sys.exit("tracker namespace anchor not found")
    t = t.replace(anchor, anchor + helper, 1)
    for inc in ("#include <chrono>", "#include <cstdlib>", "#include <mutex>", "#include <unordered_map>"):
        if inc not in t:
            t = t.replace("#include <cstdint>", "#include <cstdint>\n" + inc, 1)
    reps = [
        ("      submission_tracker_->fences_pending_.emplace_back(submission_tracker_->submission_current_,\n"
         "                                                        fence_);\n",
         "      submission_tracker_->fences_pending_.emplace_back(submission_tracker_->submission_current_,\n"
         "                                                        fence_);\n"
         "      TtStampFence(fence_);  // tt-android\n"),
        ("      if (dfn.vkGetFenceStatus(device, pending_pair.second) != VK_SUCCESS) {\n        break;\n      }\n",
         "      if (TtFenceTooYoung(pending_pair.second) ||  // tt-android\n"
         "          TtTimedStatus(dfn, device, pending_pair.second) != VK_SUCCESS) {\n        break;\n      }\n"),
        ("        if (dfn.vkWaitForFences(device, 1, &pending_pair.second, VK_TRUE, UINT64_MAX) ==\n"
         "            VK_SUCCESS) {\n          break;\n        }\n",
         "        const int64_t tt_w0 = TtNowNs();  // tt-android\n"
         "        const VkResult tt_wr =\n"
         "            dfn.vkWaitForFences(device, 1, &pending_pair.second, VK_TRUE, UINT64_MAX);\n"
         "        __atomic_fetch_add(&tt_tracker_stats[1], uint64_t(TtNowNs() - tt_w0), __ATOMIC_RELAXED);\n"
         "        __atomic_fetch_add(&tt_tracker_stats[3], 1, __ATOMIC_RELAXED);\n"
         "        if (tt_wr == VK_SUCCESS) {\n          break;\n        }\n"),
        ("      if (dfn.vkGetFenceStatus(device, pending_pair.second) == VK_SUCCESS) {\n        break;\n      }\n",
         "      if (!TtFenceTooYoung(pending_pair.second) &&  // tt-android\n"
         "          TtTimedStatus(dfn, device, pending_pair.second) == VK_SUCCESS) {\n        break;\n      }\n"),
    ]
    for old, new in reps:
        if t.count(old) != 1:
            sys.exit("tracker patch point not found: " + old.strip()[:60])
        t = t.replace(old, new)
    p.write_text(t)
    changed.append("submission tracker skips status queries on young fences")

# ---- 20. Lock / presentation timers. v32: the GPU command thread still spent ~45% blocked
#          (futex) inside RefreshGuestOutputImpl. Candidates: the shared VkQueue mutex
#          (held by the UI thread across vkQueuePresentKHR) or the refresher itself.
#          tt_tracker_stats: [4] refresher ns [5] guest-output queue-lock wait ns
#          [6] UI acquire-fence wait ns [7] UI present (incl. queue lock) ns
#          tt_fence_stats: [4] command processor submit queue-lock wait ns [5] count
pp = sdk / "src/ui/vulkan/vulkan_presenter.cpp"
t = pp.read_text()
if "tt_tracker_stats[4]" not in t:
    def add_ns(slot, var, arr="tt_tracker_stats"):
        return (f"__atomic_fetch_add(&{arr}[{slot}], uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>("
                f"std::chrono::steady_clock::now() - {var}).count()), __ATOMIC_RELAXED);")
    reps = [
        ("  bool refresher_succeeded = refresher(context);\n",
         "  const auto tt_r0 = std::chrono::steady_clock::now();  // tt-android\n"
         "  bool refresher_succeeded = refresher(context);\n"
         "  " + add_ns(4, "tt_r0") + "\n"),
        ("    const VulkanDevice::Queue::Acquisition queue_acquisition =\n"
         "        vulkan_device_->AcquireQueue(vulkan_device_->queue_family_graphics_compute(), 0);\n"
         "    if (dfn.vkQueueSubmit(queue_acquisition.queue(), 0, nullptr, fence_acqusition.fence()) !=",
         "    const auto tt_q0 = std::chrono::steady_clock::now();  // tt-android\n"
         "    const VulkanDevice::Queue::Acquisition queue_acquisition =\n"
         "        vulkan_device_->AcquireQueue(vulkan_device_->queue_family_graphics_compute(), 0);\n"
         "    " + add_ns(5, "tt_q0") + "\n"
         "    if (dfn.vkQueueSubmit(queue_acquisition.queue(), 0, nullptr, fence_acqusition.fence()) !="),
        ("      dfn.vkWaitForFences(device, 1, &tt_acquire_fence, VK_TRUE, UINT64_MAX);\n",
         "      const auto tt_a0 = std::chrono::steady_clock::now();\n"
         "      dfn.vkWaitForFences(device, 1, &tt_acquire_fence, VK_TRUE, UINT64_MAX);\n"
         "      " + add_ns(6, "tt_a0") + "\n"),
        ("    const VulkanDevice::Queue::Acquisition queue_acquisition =\n"
         "        vulkan_device_->AcquireQueue(paint_context_.present_queue_family, 0);\n"
         "    present_result = dfn.vkQueuePresentKHR(queue_acquisition.queue(), &present_info);\n",
         "    const auto tt_pr0 = std::chrono::steady_clock::now();  // tt-android\n"
         "    const VulkanDevice::Queue::Acquisition queue_acquisition =\n"
         "        vulkan_device_->AcquireQueue(paint_context_.present_queue_family, 0);\n"
         "    present_result = dfn.vkQueuePresentKHR(queue_acquisition.queue(), &present_info);\n"
         "    " + add_ns(7, "tt_pr0") + "\n"),
    ]
    for old, new in reps:
        if t.count(old) != 1:
            sys.exit("presenter timer patch point not found: " + old.strip()[:60])
        t = t.replace(old, new)
    t = t.replace("#include", "#include <chrono>\n#include <cstdint>\nextern \"C\" uint64_t tt_tracker_stats[8];\n#include", 1)
    pp.write_text(t)
    cp = sdk / "src/graphics/vulkan/command_processor.cpp"
    c = cp.read_text()
    old = ("    {\n"
           "      ui::vulkan::VulkanDevice::Queue::Acquisition queue_acquisition =\n"
           "          vulkan_device->AcquireQueue(vulkan_device->queue_family_graphics_compute(), 0);\n"
           "      const auto tt_s0")
    new = ("    {\n"
           "      const auto tt_ql0 = std::chrono::steady_clock::now();  // tt-android\n"
           "      ui::vulkan::VulkanDevice::Queue::Acquisition queue_acquisition =\n"
           "          vulkan_device->AcquireQueue(vulkan_device->queue_family_graphics_compute(), 0);\n"
           "      " + add_ns(4, "tt_ql0", "tt_fence_stats") + "\n"
           "      __atomic_fetch_add(&tt_fence_stats[5], 1, __ATOMIC_RELAXED);\n"
           "      const auto tt_s0")
    if c.count(old) != 1:
        sys.exit("cp queue-lock timer patch point not found")
    c = c.replace(old, new)
    cp.write_text(c)
    changed.append("presentation and queue-lock timers")

# ---- 21. Present without a wait semaphore. v33: the UI thread's vkQueuePresentKHR took
#          ~15 ms per present while holding the shared VkQueue lock, and the GPU command
#          thread waited ~22 ms per frame for that lock. On Android the driver turns a
#          present that waits on a semaphore into vkQueueSignalReleaseImageANDROID, which
#          can fall back to idling the whole queue. Now the UI thread waits for its own
#          paint submission's fence (without the queue lock) and presents with no
#          semaphores, so the present is quick. TT_PRESENT_FENCE=off disables (with 13).
pp = sdk / "src/ui/vulkan/vulkan_presenter.cpp"
t = pp.read_text()
if "tt-android: present without semaphore" not in t:
    reps = [
        ("  submit_info.signalSemaphoreCount = 1;\n  submit_info.pSignalSemaphores = &present_semaphore;\n",
         "  // tt-android: present without semaphore (see patch notes)\n"
         "  submit_info.signalSemaphoreCount = tt_fence_acquire ? 0 : 1;\n"
         "  submit_info.pSignalSemaphores = tt_fence_acquire ? nullptr : &present_semaphore;\n"),
        ("  VkPresentInfoKHR present_info;\n  present_info.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;\n"
         "  present_info.pNext = nullptr;\n  present_info.waitSemaphoreCount = 1;\n"
         "  present_info.pWaitSemaphores = &present_semaphore;\n",
         "  if (tt_fence_acquire) {\n"
         "    // Wait for the paint on the CPU instead of in the present.\n"
         "    const auto tt_pw0 = std::chrono::steady_clock::now();\n"
         "    paint_context_.submission_tracker.AwaitSubmissionCompletion(\n"
         "        paint_context_.submission_tracker.GetCurrentSubmission() - 1);\n"
         "    __atomic_fetch_add(&tt_tracker_stats[6], uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
         "        std::chrono::steady_clock::now() - tt_pw0).count()), __ATOMIC_RELAXED);\n"
         "  }\n"
         "  VkPresentInfoKHR present_info;\n  present_info.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;\n"
         "  present_info.pNext = nullptr;\n"
         "  present_info.waitSemaphoreCount = tt_fence_acquire ? 0 : 1;\n"
         "  present_info.pWaitSemaphores = tt_fence_acquire ? nullptr : &present_semaphore;\n"),
    ]
    for old, new in reps:
        if t.count(old) != 1:
            sys.exit("present-without-semaphore patch point not found: " + old.strip()[:60])
        t = t.replace(old, new)
    pp.write_text(t)
    changed.append("present without wait semaphores (paint awaited on the CPU)")

# ---- 22. Guest vblank rate override (TT_VBLANK_HZ). With the host stalls gone (v34) the
#          game still sits near 30-35 FPS while every thread has spare time; the game's
#          frame pacing waits on the GPU interrupt that the vsync worker fires 60 times a
#          second, so a frame that misses one vblank waits for the next. This lets that
#          rate be raised as an experiment (the VdSwap limiter still caps presentation).
p = sdk / "src/graphics/graphics_system.cpp"
t = p.read_text()
if "TT_VBLANK_HZ" not in t:
    old = ("        uint64_t vsync_interval_ticks =\n"
           "            std::max(uint64_t(1), uint64_t(double(guest_tick_frequency) / refresh_rate_hz));\n")
    new = old + ("        if (const char* tt_hz = std::getenv(\"TT_VBLANK_HZ\")) {  // tt-android\n"
                 "          const double hz = std::atof(tt_hz);\n"
                 "          if (hz >= 30.0 && hz <= 1000.0) {\n"
                 "            vsync_interval_ticks = std::max(uint64_t(1), uint64_t(double(guest_tick_frequency) / hz));\n"
                 "            REXLOG_INFO(\"tt: guest vblank interrupts at {} Hz\", hz);\n"
                 "          }\n"
                 "        }\n")
    if t.count(old) != 1:
        sys.exit("vblank patch point not found")
    t = t.replace(old, new)
    if "#include <cstdlib>" not in t:
        t = t.replace("#include", "#include <cstdlib>\n#include", 1)
    p.write_text(t)
    changed.append("guest vblank rate override (TT_VBLANK_HZ)")

# ---- 23. Where the command processor sleeps now. v35: ~68% of the GPU command thread is in
#          the GPU-progress marker's wait loop (sleep_for), reached from IssueDraw rather
#          than from the frame throttle. Suspect: sampler-pool overflow, which makes a draw
#          wait for earlier submissions to finish before samplers can be reused.
#          tt_fence_stats: [6] marker-wait ns [7] marker waits;
#          tt_frame_stats[7]: sampler overflows (draw waits); logs the sampler budget once.
cp = sdk / "src/graphics/vulkan/command_processor.cpp"
c = cp.read_text()
if "tt_fence_stats[6]" not in c:
    old = ("      while (await_submission > tt_done && std::chrono::steady_clock::now() < tt_deadline) {\n"
           "        std::this_thread::sleep_for(std::chrono::microseconds(50));\n"
           "        tt_done = tt_marker::Done(*this, submission_completed_);\n"
           "      }\n")
    new = ("      const auto tt_mw0 = std::chrono::steady_clock::now();\n"
           "      __atomic_fetch_add(&tt_fence_stats[7], 1, __ATOMIC_RELAXED);\n" + old +
           "      __atomic_fetch_add(&tt_fence_stats[6], uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
           "          std::chrono::steady_clock::now() - tt_mw0).count()), __ATOMIC_RELAXED);\n")
    if c.count(old) != 1:
        sys.exit("marker wait timer patch point not found (needs 18)")
    c = c.replace(old, new)
    old = "    CheckSubmissionFenceAndDeviceLoss(sampler_overflow_await_submission);\n"
    if c.count(old) != 1:
        sys.exit("sampler overflow patch point not found")
    c = c.replace(old, "    __atomic_fetch_add(&tt_frame_stats[7], 1, __ATOMIC_RELAXED);  // tt-android\n" + old)
    if 'extern "C" uint64_t tt_frame_stats' not in c:
        ns = c.find("\nnamespace")
        c = c[:ns] + '\nextern "C" uint64_t tt_frame_stats[8];\n' + c[ns:]
    cp.write_text(c)
    tc = sdk / "src/graphics/vulkan/texture_cache.cpp"
    t = tc.read_text()
    old = ("  sampler_max_count_ =\n"
           "      device_properties.maxSamplerAllocationCount - ui::vulkan::UISamplers::kSamplerCount - 16;\n")
    if t.count(old) != 1:
        sys.exit("sampler budget patch point not found")
    t = t.replace(old, old + "  REXLOG_INFO(\"tt: sampler budget {} (device max {})\", sampler_max_count_,  // tt-android\n"
                             "              device_properties.maxSamplerAllocationCount);\n")
    tc.write_text(t)
    changed.append("marker-wait and sampler-overflow counters")

# ---- 24. Frame timeline. v40: GPU work is ~10 ms per frame but frames take ~29 ms, and the
#          60/30/20 Hz screen test changed nothing. Record a short timeline of what happens
#          inside each frame (CPU submit, GPU start/end of every submission, the command
#          processor's waits on the GPU, guest swaps) so the profiler can dump it.
#          tt_timeline: 4096 events x {time ns, type, a, b}; tt_timeline_pos: events written.
dcb = sdk / "src/graphics/vulkan/deferred_command_buffer.cpp"
t = dcb.read_text()
if "tt_timeline" not in t:
    ring = """
// ---- tt-android: frame timeline (read by the profiler via dlsym)
extern "C" __attribute__((visibility("default"))) uint64_t tt_timeline[4096 * 4] = {};
extern "C" __attribute__((visibility("default"))) uint64_t tt_timeline_pos[2] = {};
extern "C" __attribute__((visibility("default"))) void TtTimelineEventAt(int64_t t_ns, uint32_t type,
                                                                         uint64_t a, uint64_t b) {
  if (t_ns <= 0) return;
  const uint64_t i = __atomic_fetch_add(&tt_timeline_pos[0], 1, __ATOMIC_RELAXED) & 4095;
  tt_timeline[i * 4 + 1] = type;
  tt_timeline[i * 4 + 2] = a;
  tt_timeline[i * 4 + 3] = b;
  __atomic_store_n(&tt_timeline[i * 4 + 0], uint64_t(t_ns), __ATOMIC_RELEASE);
}
extern "C" __attribute__((visibility("default"))) void TtTimelineEvent(uint32_t type, uint64_t a,
                                                                       uint64_t b) {
  TtTimelineEventAt(std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count(),
                    type, a, b);
}
"""
    anchor = "namespace rex::graphics::vulkan {\n"
    if t.count(anchor) != 1:
        sys.exit("timeline: namespace anchor not found")
    t = t.replace(anchor, ring.lstrip("\n") + "\n" + anchor, 1)
    reps = [
        # 1 = CPU submits submission a
        ("  s.count = 0;\n  s.pending = true;\n  s.collected = false;\n  s.cpu_submit_ns = NowNs();\n",
         "  s.count = 0;\n  s.pending = true;\n  s.collected = false;\n  s.cpu_submit_ns = NowNs();\n"
         "  TtTimelineEventAt(s.cpu_submit_ns, 1, submission, 0);  // tt-android timeline\n"),
        # 2/3 = GPU starts / finishes submission a (b = GPU ns of that submission)
        ("    __atomic_fetch_add(&tt_gputime_stats[14], 1, __ATOMIC_RELAXED);\n",
         "    __atomic_fetch_add(&tt_gputime_stats[14], 1, __ATOMIC_RELAXED);\n"
         "    TtTimelineEventAt(s.gpu_start_ns - offset_min, 2, s.submission, 0);  // tt-android timeline\n"
         "    TtTimelineEventAt(s.gpu_end_ns - offset_min, 3, s.submission,\n"
         "                      uint64_t(s.gpu_end_ns - s.gpu_start_ns));\n"),
    ]
    for old, new in reps:
        if t.count(old) != 1:
            sys.exit("timeline patch point not found: " + old.strip()[:60])
        t = t.replace(old, new)
    dcb.write_text(t)
    c = cp.read_text()
    old = ("      const auto tt_mw0 = std::chrono::steady_clock::now();\n"
           "      __atomic_fetch_add(&tt_fence_stats[7], 1, __ATOMIC_RELAXED);\n")
    if c.count(old) != 1:
        sys.exit("timeline: marker wait point not found (needs 23)")
    # 4 = command processor starts waiting for submission a (b = GPU finished up to b)
    c = c.replace(old, old + "      TtTimelineEvent(4, await_submission, tt_done);  // tt-android timeline\n")
    old = ("      __atomic_fetch_add(&tt_fence_stats[6], uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
           "          std::chrono::steady_clock::now() - tt_mw0).count()), __ATOMIC_RELAXED);\n")
    if c.count(old) != 1:
        sys.exit("timeline: marker wait end not found")
    # 5 = wait over (b = GPU finished up to b)
    c = c.replace(old, old + "      TtTimelineEvent(5, await_submission, tt_done);  // tt-android timeline\n")
    # 6 = command processor ends a submission (a = submission, b = 1 if it ends a frame)
    old = "bool VulkanCommandProcessor::EndSubmission(bool is_swap) {\n"
    if c.count(old) != 1:
        sys.exit("timeline: EndSubmission not found")
    c = c.replace(old, old + "  TtTimelineEvent(6, GetCurrentSubmission(), is_swap ? 1 : 0);  // tt-android timeline\n")
    ns = c.find("\nnamespace")
    c = (c[:ns] + '\nextern "C" void TtTimelineEvent(uint32_t type, uint64_t a, uint64_t b);\n' + c[ns:])
    cp.write_text(c)
    changed.append("frame timeline events")

# ---- 25. v41 timeline: every frame the command processor waits ~17 ms for a submission
#          the GPU had finished ~50-70 ms earlier. The marker value only becomes visible
#          to the CPU when some later submission flushes the GPU caches (the 60 Hz guest
#          output refresh), so the wait lasts until the next refresh. Fix: while waiting,
#          also ask the driver whether that submission's fence is signalled (cheap when it
#          is, which is nearly always). Checked at once, then every 0.5 ms.
#          TT_FENCE_CHECK=off restores marker-only waiting.
#          Timeline: event 8 = wait ended by the fence (b = ns that fence query took),
#          tt_fence_stats[8] fence-ended waits, [9] ns in those fence queries.
c = cp.read_text()
if "tt-android: fence check in marker wait" not in c:
    old = ("      while (await_submission > tt_done && std::chrono::steady_clock::now() < tt_deadline) {\n"
           "        std::this_thread::sleep_for(std::chrono::microseconds(50));\n"
           "        tt_done = tt_marker::Done(*this, submission_completed_);\n"
           "      }\n")
    new = ("      // tt-android: fence check in marker wait (patch 25)\n"
           "      static const bool tt_fence_check = [] {\n"
           "        const char* v = std::getenv(\"TT_FENCE_CHECK\");\n"
           "        return !(v && std::string_view(v) == \"off\");\n"
           "      }();\n"
           "      auto tt_next_check = std::chrono::steady_clock::now();\n"
           "      while (await_submission > tt_done && std::chrono::steady_clock::now() < tt_deadline) {\n"
           "        if (tt_fence_check && std::chrono::steady_clock::now() >= tt_next_check &&\n"
           "            await_submission > submission_completed_) {\n"
           "          const size_t tt_i = size_t(await_submission - submission_completed_ - 1);\n"
           "          if (tt_i < submissions_in_flight_fences_.size()) {\n"
           "            const auto tt_q0 = std::chrono::steady_clock::now();\n"
           "            const VkResult tt_st = dfn.vkGetFenceStatus(device, submissions_in_flight_fences_[tt_i]);\n"
           "            const uint64_t tt_q_ns = uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
           "                std::chrono::steady_clock::now() - tt_q0).count());\n"
           "            __atomic_fetch_add(&tt_fence_stats[9], tt_q_ns, __ATOMIC_RELAXED);\n"
           "            if (tt_st == VK_SUCCESS) {\n"
           "              // Submissions complete in order: everything up to this one is done.\n"
           "              __atomic_fetch_add(&tt_fence_stats[8], 1, __ATOMIC_RELAXED);\n"
           "              TtTimelineEvent(8, await_submission, tt_q_ns);\n"
           "              tt_done = await_submission;\n"
           "              break;\n"
           "            }\n"
           "          }\n"
           "          tt_next_check = std::chrono::steady_clock::now() + std::chrono::microseconds(500);\n"
           "        }\n"
           "        std::this_thread::sleep_for(std::chrono::microseconds(50));\n"
           "        tt_done = std::max(tt_done, tt_marker::Done(*this, submission_completed_));\n"
           "      }\n")
    if c.count(old) != 1:
        sys.exit("fence check: marker wait loop not found")
    c = c.replace(old, new)
    for inc in ("#include <algorithm>", "#include <string_view>", "#include <cstdlib>"):
        if inc not in c:
            c = c.replace("#include", inc + "\n#include", 1)
    cp.write_text(c)
    changed.append("marker wait also checks the submission fence")

# repair: the first v20 script declared tt_work_stats twice in command_processor.cpp
f = sdk / "src/graphics/vulkan/command_processor.cpp"
t = f.read_text()
decl = ("\n// tt-android: [0] draws [1] resolves [2] texture loads [3] texture bytes\n"
        "// [4] RT transfer passes [5] RT transfers [6] upload calls [7] upload bytes\n"
        "extern \"C\" __attribute__((visibility(\"default\"))) uint64_t tt_work_stats[16] = {};\n")
if t.count(decl) > 1:
    first = t.find(decl) + len(decl)
    t = t[:first] + t[first:].replace(decl, "")
    f.write_text(t)
    changed.append("removed duplicate tt_work_stats declaration")

# ---- 11. Tiling counters: bin-select writes and predicated packets run/skipped per frame
p = sdk / "src/graphics/command_processor.cpp"
t = p.read_text()
if "tt_frame_stats[5]" not in t:
    o1 = """  if (packet & 1) {
    bool any_pass = (bin_select_ & bin_mask_) != 0;
    if (!any_pass || opcode == PM4_XE_SWAP) {
      reader->AdvanceRead(count * sizeof(uint32_t));
      return true;
    }
  }"""
    n1 = """  if (packet & 1) {
    bool any_pass = (bin_select_ & bin_mask_) != 0;
    if (!any_pass || opcode == PM4_XE_SWAP) {
      __atomic_fetch_add(&tt_frame_stats[4], 1, __ATOMIC_RELAXED);  // tt-android: skipped
      reader->AdvanceRead(count * sizeof(uint32_t));
      return true;
    }
    __atomic_fetch_add(&tt_frame_stats[6], 1, __ATOMIC_RELAXED);  // tt-android: predicated, run
  }"""
    if t.count(o1) != 1:
        sys.exit("predication patch point not found")
    t = t.replace(o1, n1)
    n = 0
    for op in ("PM4_SET_BIN_SELECT_LO: {", "PM4_SET_BIN_SELECT: {"):
        o = f"    case {op}\n"
        if t.count(o) == 1:
            t = t.replace(o, o + "      __atomic_fetch_add(&tt_frame_stats[5], 1, __ATOMIC_RELAXED);  // tt-android\n")
            n += 1
    if n == 0:
        sys.exit("bin select patch point not found")
    p.write_text(t)
    changed.append("tiling counters (bin select, predicated packets)")

print("\n".join(changed) if changed else "SDK Vulkan fixes already applied")
