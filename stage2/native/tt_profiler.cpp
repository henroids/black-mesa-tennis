// tt-android: built-in sampling profiler (Android / Linux only).
//
// Enabled when the launcher sets TT_PROFILE=<logs dir>. A sampler thread picks the
// busy threads of the game process once a second (by CPU time in /proc), then about
// 1000 times a second interrupts each of them with SIGPROF. The signal handler records
// the program counter and walks the frame-pointer chain. Recompiled game code is mapped
// back to its guest function (sub_XXXXXXXX) through the generated PPCFuncMappings table,
// so no symbol files are needed; anything else is named with dladdr.
//
// Every 10 seconds <logs>/profile.txt is rewritten with, per busy thread:
//   SELF       where the thread actually was (leaf function)
//   GUEST      the innermost guest function on the stack (who is responsible)
//   INCLUSIVE  every function on the stack (where time is spent below)
// for the last 30 seconds and for the whole session.

#if defined(__ANDROID__) || defined(__linux__)

#include <rex/ppc/func.h>
#include <rex/logging.h>

#include <algorithm>
#include <cmath>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cxxabi.h>
#include <deque>
#include <dirent.h>
#include <dlfcn.h>
#include <signal.h>
#include <string>
#include <sys/syscall.h>
#include <thread>
#include <ucontext.h>
#include <unistd.h>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

constexpr int kMaxFrames = 24;
constexpr int kMaxTargets = 10;
constexpr auto kTick = std::chrono::microseconds(1000);
constexpr int kWindowSeconds = 10;
constexpr int kWindowsKept = 3;  // "last 30 seconds"

// ---------------------------------------------------------------- signal handler state
// Only one request is outstanding at a time; the sampler waits for it before sending
// the next one, so the handler never races with updates to g_ranges.
std::atomic<int> g_req_tid{0};
std::atomic<uint32_t> g_req_seq{0};
std::atomic<uint32_t> g_done_seq{0};
uintptr_t g_frames[kMaxFrames];
int g_depth = 0;

struct Range {
  uintptr_t begin, end;
};
std::vector<Range> g_ranges;  // readable, writable mappings (stacks live here)

bool Readable(uintptr_t addr, size_t size) {
  size_t lo = 0, hi = g_ranges.size();
  while (lo < hi) {
    size_t mid = (lo + hi) / 2;
    if (g_ranges[mid].end <= addr) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo < g_ranges.size() && g_ranges[lo].begin <= addr && addr + size <= g_ranges[lo].end;
}

void OnSample(int, siginfo_t*, void* context) {
  const int saved_errno = errno;
  const uint32_t seq = g_req_seq.load(std::memory_order_acquire);
  if (static_cast<int>(syscall(SYS_gettid)) != g_req_tid.load(std::memory_order_relaxed) ||
      g_done_seq.load(std::memory_order_relaxed) == seq) {
    errno = saved_errno;
    return;
  }
  auto* uc = static_cast<ucontext_t*>(context);
  int depth = 0;
#if defined(__aarch64__)
  g_frames[depth++] = static_cast<uintptr_t>(uc->uc_mcontext.pc);
  g_frames[depth++] = static_cast<uintptr_t>(uc->uc_mcontext.regs[30]);  // lr (leaf caller)
  uintptr_t fp = static_cast<uintptr_t>(uc->uc_mcontext.regs[29]);
#elif defined(__x86_64__)
  g_frames[depth++] = static_cast<uintptr_t>(uc->uc_mcontext.gregs[REG_RIP]);
  uintptr_t fp = static_cast<uintptr_t>(uc->uc_mcontext.gregs[REG_RBP]);
#else
  uintptr_t fp = 0;
#endif
  while (depth < kMaxFrames && fp && (fp & 7) == 0 && Readable(fp, 16)) {
    const uintptr_t next = reinterpret_cast<const uintptr_t*>(fp)[0];
    const uintptr_t ret = reinterpret_cast<const uintptr_t*>(fp)[1];
    if (!ret) break;
    g_frames[depth++] = ret;
    if (next <= fp) break;
    fp = next;
  }
  g_depth = depth;
  g_done_seq.store(seq, std::memory_order_release);
  errno = saved_errno;
}

// ---------------------------------------------------------------- symbolisation
std::vector<std::pair<uintptr_t, uint32_t>> g_guest;  // host entry -> guest address, sorted

void BuildGuestTable() {
  for (int i = 0; PPCFuncMappings[i].guest != 0; ++i) {
    if (PPCFuncMappings[i].host) {
      g_guest.emplace_back(reinterpret_cast<uintptr_t>(PPCFuncMappings[i].host),
                           static_cast<uint32_t>(PPCFuncMappings[i].guest));
    }
  }
  std::sort(g_guest.begin(), g_guest.end());
  g_guest.erase(std::unique(g_guest.begin(), g_guest.end(),
                            [](auto& a, auto& b) { return a.first == b.first; }),
                g_guest.end());
}

// Location names are interned; samples store small ids.
std::vector<std::string> g_names;
std::unordered_map<std::string, uint32_t> g_name_ids;
std::unordered_map<uintptr_t, uint32_t> g_pc_cache;
std::vector<uint8_t> g_is_guest;

uint32_t Intern(const std::string& name, bool guest) {
  auto [it, inserted] = g_name_ids.emplace(name, static_cast<uint32_t>(g_names.size()));
  if (inserted) {
    g_names.push_back(name);
    g_is_guest.push_back(guest ? 1 : 0);
  }
  return it->second;
}

std::string HostName(uintptr_t pc) {
  Dl_info info{};
  char buffer[96];
  if (dladdr(reinterpret_cast<void*>(pc), &info) && info.dli_fname) {
    const char* lib = std::strrchr(info.dli_fname, '/');
    lib = lib ? lib + 1 : info.dli_fname;
    if (info.dli_sname) {
      int status = 0;
      char* demangled = abi::__cxa_demangle(info.dli_sname, nullptr, nullptr, &status);
      std::string name = status == 0 && demangled ? demangled : info.dli_sname;
      std::free(demangled);
      if (name.size() > 110) name = name.substr(0, 107) + "...";
      return name + "  [" + lib + "]";
    }
    std::snprintf(buffer, sizeof buffer, "%s+0x%zx", lib,
                  static_cast<size_t>(pc - reinterpret_cast<uintptr_t>(info.dli_fbase)));
    return buffer;
  }
  std::snprintf(buffer, sizeof buffer, "?0x%zx", static_cast<size_t>(pc));
  return buffer;
}

uint32_t Resolve(uintptr_t pc) {
  auto cached = g_pc_cache.find(pc);
  if (cached != g_pc_cache.end()) return cached->second;
  uint32_t id;
  auto it = std::upper_bound(g_guest.begin(), g_guest.end(), std::make_pair(pc, UINT32_MAX));
  bool guest = false;
  if (it != g_guest.begin()) {
    auto entry = std::prev(it);
    const uintptr_t limit = it != g_guest.end() ? it->first : entry->first + 0x10000;
    if (pc < limit && pc - entry->first < 0x40000) {
      char buffer[32];
      std::snprintf(buffer, sizeof buffer, "sub_%08X", entry->second);
      id = Intern(buffer, true);
      guest = true;
    }
  }
  if (!guest) id = Intern(HostName(pc), false);
  g_pc_cache.emplace(pc, id);
  return id;
}

// ---------------------------------------------------------------- aggregation
struct Window {
  std::unordered_map<uint64_t, uint32_t> self, incl, owner;
  std::unordered_map<uint32_t, uint32_t> thread_samples;
  void Clear() {
    self.clear();
    incl.clear();
    owner.clear();
    thread_samples.clear();
  }
  void Merge(const Window& o) {
    for (auto& [k, v] : o.self) self[k] += v;
    for (auto& [k, v] : o.incl) incl[k] += v;
    for (auto& [k, v] : o.owner) owner[k] += v;
    for (auto& [k, v] : o.thread_samples) thread_samples[k] += v;
  }
};

std::vector<std::string> g_thread_names;
std::unordered_map<std::string, uint32_t> g_thread_ids;

uint32_t ThreadId(const std::string& name) {
  auto [it, inserted] = g_thread_ids.emplace(name, static_cast<uint32_t>(g_thread_names.size()));
  if (inserted) g_thread_names.push_back(name);
  return it->second;
}

uint64_t Key(uint32_t thread, uint32_t loc) { return (uint64_t(thread) << 32) | loc; }

void Record(Window& w, uint32_t thread, const uintptr_t* frames, int depth) {
  if (depth <= 0) return;
  ++w.thread_samples[thread];
  ++w.self[Key(thread, Resolve(frames[0]))];
  uint32_t seen[kMaxFrames];
  int seen_count = 0;
  bool owner_found = false;
  for (int i = 0; i < depth; ++i) {
    // frames[1] is the link register: only meaningful when the leaf has no frame yet.
    const uint32_t loc = Resolve(i == 0 ? frames[i] : frames[i] - 1);
    if (!owner_found && g_is_guest[loc]) {
      ++w.owner[Key(thread, loc)];
      owner_found = true;
    }
    bool dup = false;
    for (int j = 0; j < seen_count; ++j) dup |= seen[j] == loc;
    if (!dup) {
      seen[seen_count++] = loc;
      ++w.incl[Key(thread, loc)];
    }
  }
  if (!owner_found) ++w.owner[Key(thread, Intern("(no guest code on stack)", false))];
}

// ---------------------------------------------------------------- /proc helpers
std::string ReadSmall(const char* path) {
  std::string out;
  if (FILE* f = std::fopen(path, "r")) {
    char buffer[512];
    size_t n = std::fread(buffer, 1, sizeof buffer, f);
    out.assign(buffer, n);
    std::fclose(f);
  }
  return out;
}

struct TaskInfo {
  int tid;
  std::string name;
  uint64_t cpu_ticks;
};

std::vector<TaskInfo> ListTasks() {
  std::vector<TaskInfo> tasks;
  DIR* dir = opendir("/proc/self/task");
  if (!dir) return tasks;
  while (dirent* e = readdir(dir)) {
    if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
    int tid = std::atoi(e->d_name);
    char path[64];
    std::snprintf(path, sizeof path, "/proc/self/task/%d/stat", tid);
    std::string stat = ReadSmall(path);
    size_t open = stat.find('('), close = stat.rfind(')');
    if (open == std::string::npos || close == std::string::npos) continue;
    TaskInfo t{tid, stat.substr(open + 1, close - open - 1), 0};
    // Fields after the name: state(3) ... utime(14) stime(15).
    const char* p = stat.c_str() + close + 2;
    int field = 3;
    unsigned long long utime = 0, stime = 0;
    while (*p && field <= 15) {
      if (field == 14) utime = std::strtoull(p, nullptr, 10);
      if (field == 15) stime = std::strtoull(p, nullptr, 10);
      while (*p && *p != ' ') ++p;
      while (*p == ' ') ++p;
      ++field;
    }
    t.cpu_ticks = utime + stime;
    tasks.push_back(std::move(t));
  }
  closedir(dir);
  return tasks;
}

void RefreshRanges() {
  std::vector<Range> ranges;
  if (FILE* f = std::fopen("/proc/self/maps", "r")) {
    char line[512];
    while (std::fgets(line, sizeof line, f)) {
      unsigned long long begin = 0, end = 0;
      char perms[8] = {0};
      if (std::sscanf(line, "%llx-%llx %7s", &begin, &end, perms) == 3 && perms[0] == 'r' &&
          perms[1] == 'w') {
        if (!ranges.empty() && ranges.back().end == begin) {
          ranges.back().end = end;
        } else {
          ranges.push_back({static_cast<uintptr_t>(begin), static_cast<uintptr_t>(end)});
        }
      }
    }
    std::fclose(f);
  }
  g_ranges.swap(ranges);
}

// ---------------------------------------------------------------- hot code dump
// Set by the app: maps a guest address to host memory (nullptr if unmapped).
const uint8_t* (*g_translate)(uint32_t) = nullptr;
bool g_code_dumped = false;

// Writes the guest machine code of the hottest game functions to hotcode.txt so they
// can be disassembled offline (debugging what a busy thread is waiting on).
void DumpHotCode(const std::string& dir, const Window& w) {
  if (!g_translate) return;
  std::vector<std::pair<uint32_t, uint32_t>> rows;  // count, loc
  for (auto& [k, v] : w.self) {
    if (g_is_guest[static_cast<uint32_t>(k)]) rows.emplace_back(v, static_cast<uint32_t>(k));
  }
  std::sort(rows.begin(), rows.end(), [](auto& a, auto& b) { return a.first > b.first; });
  FILE* f = std::fopen((dir + "/hotcode.txt").c_str(), "w");
  if (!f) return;
  std::unordered_set<uint32_t> done;
  for (size_t i = 0; i < rows.size() && done.size() < 4; ++i) {
    const std::string& name = g_names[rows[i].second];
    uint32_t guest = static_cast<uint32_t>(std::strtoul(name.c_str() + 4, nullptr, 16));
    if (!guest || !done.insert(guest).second) continue;
    // Size: up to the next mapped guest function, at most 2 KB.
    uint32_t end = guest + 0x800;
    for (auto& [host, g] : g_guest) {
      if (g > guest && g < end) end = g;
    }
    std::fprintf(f, "# %s  samples=%u  size=0x%X\n", name.c_str(), rows[i].first, end - guest);
    for (uint32_t a = guest; a < end; a += 16) {
      std::fprintf(f, "%08X:", a);
      for (uint32_t b = a; b < a + 16 && b < end; b += 4) {
        const uint8_t* p = g_translate(b);
        if (!p) break;
        std::fprintf(f, " %02X%02X%02X%02X", p[0], p[1], p[2], p[3]);
      }
      std::fputc('\n', f);
    }
  }
  std::fclose(f);
}

// ---------------------------------------------------------------- tiling scan
// ---------------------------------------------------------------- report
// Counters defined by the patched SDK (librexruntime.so); looked up at run time so the
// profiler still works against an unpatched runtime.
const uint64_t* FindStats(const char* name) {
  if (void* p = dlsym(RTLD_DEFAULT, name)) return static_cast<const uint64_t*>(p);
  // The GPU backend is a plugin opened with RTLD_LOCAL: look inside it directly.
  for (const char* lib : {"librexgpu-xenos.so", "librexruntime.so"}) {
    if (void* handle = dlopen(lib, RTLD_NOW | RTLD_NOLOAD)) {
      if (void* p = dlsym(handle, name)) return static_cast<const uint64_t*>(p);
    }
  }
  return nullptr;
}

void WriteSection(FILE* f, const char* title, const std::unordered_map<uint64_t, uint32_t>& map,
                  uint32_t thread, uint32_t total, size_t limit) {
  std::vector<std::pair<uint32_t, uint32_t>> rows;  // count, loc
  for (auto& [k, v] : map) {
    if (static_cast<uint32_t>(k >> 32) == thread) rows.emplace_back(v, static_cast<uint32_t>(k));
  }
  std::sort(rows.begin(), rows.end(), [](auto& a, auto& b) { return a.first > b.first; });
  std::fprintf(f, "  %s\n", title);
  for (size_t i = 0; i < rows.size() && i < limit; ++i) {
    std::fprintf(f, "    %5.1f%%  %7u  %s\n", 100.0 * rows[i].first / std::max(total, 1u),
                 rows[i].first, g_names[rows[i].second].c_str());
  }
}

void WriteWindow(FILE* f, const char* title, const Window& w, double seconds) {
  std::fprintf(f, "\n################ %s (%.0f s) ################\n", title, seconds);
  std::vector<std::pair<uint32_t, uint32_t>> threads;
  for (auto& [t, n] : w.thread_samples) threads.emplace_back(n, t);
  std::sort(threads.begin(), threads.end(), [](auto& a, auto& b) { return a.first > b.first; });
  for (auto& [n, t] : threads) {
    std::fprintf(f, "\n=== thread \"%s\": %u samples ===\n", g_thread_names[t].c_str(), n);
    if (n < 50) continue;
    WriteSection(f, "SELF (where the thread was)", w.self, t, n, 40);
    WriteSection(f, "GUEST (innermost game function responsible)", w.owner, t, n, 30);
    WriteSection(f, "INCLUSIVE (on the stack)", w.incl, t, n, 40);
  }
}

void RunProfiler(std::string dir) {
  BuildGuestTable();
  RefreshRanges();
  struct sigaction sa {};
  sa.sa_sigaction = OnSample;
  sa.sa_flags = SA_SIGINFO | SA_RESTART;
  sigemptyset(&sa.sa_mask);
  if (sigaction(SIGPROF, &sa, nullptr) != 0) {
    REXLOG_WARN("profiler: sigaction failed ({})", errno);
    return;
  }
  const int pid = getpid();
  const int self_tid = static_cast<int>(syscall(SYS_gettid));
  REXLOG_INFO("profiler: {} guest functions mapped, writing {}/profile.txt", g_guest.size(), dir);

  using clock = std::chrono::steady_clock;
  const auto start = clock::now();
  Window current, total;
  std::deque<Window> recent;
  std::unordered_map<int, uint64_t> last_ticks;
  std::unordered_map<int, double> cpu_pct;
  std::vector<std::pair<int, uint32_t>> targets;  // tid, thread id
  auto next_refresh = clock::now();
  auto next_ranges = clock::now() + std::chrono::seconds(5);
  auto next_report = clock::now() + std::chrono::seconds(kWindowSeconds);
  auto next_tick = clock::now();
  const long hz = sysconf(_SC_CLK_TCK) > 0 ? sysconf(_SC_CLK_TCK) : 100;
  uint64_t sent = 0, missed = 0;
  // Counters from the patched SDK (found at the first report: the GPU plugin loads late).
  const uint64_t* frame = nullptr;
  const uint64_t* work = nullptr;
  const uint64_t* tex = nullptr;
  uint64_t frame_prev[8] = {}, work_prev[16] = {}, tex_prev[4] = {};
  auto stats_prev_time = start;
  uintptr_t frames[kMaxFrames];

  for (;;) {
    auto now = clock::now();
    if (now >= next_refresh) {
      next_refresh = now + std::chrono::seconds(1);
      std::vector<std::pair<double, std::pair<int, std::string>>> busy;
      std::unordered_map<int, uint64_t> ticks;
      for (auto& t : ListTasks()) {
        ticks[t.tid] = t.cpu_ticks;
        auto prev = last_ticks.find(t.tid);
        double pct = prev == last_ticks.end() ? 0.0 : 100.0 * (t.cpu_ticks - prev->second) / hz;
        cpu_pct[t.tid] = pct;
        if (t.tid != self_tid && pct >= 5.0) busy.push_back({pct, {t.tid, t.name}});
      }
      last_ticks.swap(ticks);
      std::sort(busy.begin(), busy.end(), [](auto& a, auto& b) { return a.first > b.first; });
      targets.clear();
      for (size_t i = 0; i < busy.size() && i < kMaxTargets; ++i) {
        targets.emplace_back(busy[i].second.first, ThreadId(busy[i].second.second));
      }
    }
    if (now >= next_ranges) {
      next_ranges = now + std::chrono::seconds(5);
      RefreshRanges();
    }

    for (auto& [tid, thread] : targets) {
      const uint32_t seq = g_req_seq.load(std::memory_order_relaxed) + 1;
      g_req_tid.store(tid, std::memory_order_relaxed);
      g_req_seq.store(seq, std::memory_order_release);
      ++sent;
      if (syscall(SYS_tgkill, pid, tid, SIGPROF) != 0) continue;
      const auto deadline = clock::now() + std::chrono::milliseconds(20);
      bool done = false;
      while (clock::now() < deadline) {
        if (g_done_seq.load(std::memory_order_acquire) == seq) {
          done = true;
          break;
        }
        std::this_thread::yield();
      }
      if (!done) {
        // Mark the request answered so a late handler does nothing.
        g_done_seq.store(seq, std::memory_order_release);
        ++missed;
        continue;
      }
      const int depth = g_depth;
      std::memcpy(frames, g_frames, sizeof(uintptr_t) * depth);
      Record(current, thread, frames, depth);
    }

    if (now >= next_report) {
      next_report = now + std::chrono::seconds(kWindowSeconds);
      total.Merge(current);
      recent.push_back(std::move(current));
      current = Window{};
      while (recent.size() > kWindowsKept) recent.pop_front();
      Window last;
      for (auto& w : recent) last.Merge(w);

      const double session = std::chrono::duration<double>(now - start).count();
      const double stat_secs = std::chrono::duration<double>(now - stats_prev_time).count();
      stats_prev_time = now;
      std::string tmp = dir + "/profile.txt.tmp";
      if (FILE* f = std::fopen(tmp.c_str(), "w")) {
        std::fprintf(f, "Rockstar Table Tennis sampling profile\n");
        std::fprintf(f, "session %.0f s, %zu guest functions mapped, %llu samples requested, "
                        "%llu missed\n", session, g_guest.size(),
                     static_cast<unsigned long long>(sent), static_cast<unsigned long long>(missed));
        if (!frame) frame = FindStats("tt_frame_stats");
        if (!work) work = FindStats("tt_work_stats");
        if (!tex) tex = FindStats("tt_tex_stats");
        if (const char* described = std::getenv("TT_SETTINGS")) {
          std::fprintf(f, "session settings: %s\n", described);
        }
        if (frame) {
          const double swaps = std::max<double>(1.0, double(frame[3] - frame_prev[3]));
          std::fprintf(f, "frame pacing last %.0f s: %.1f guest vblanks/s, %.1f game frames/s, "
                          "%.1f CP wait-for-memory sleeps/s (%.0f ms/s)\n",
                       stat_secs, (frame[0] - frame_prev[0]) / stat_secs,
                       (frame[3] - frame_prev[3]) / stat_secs, (frame[1] - frame_prev[1]) / stat_secs,
                       (frame[2] - frame_prev[2]) / 1e6 / stat_secs);
          if (work) {
            auto per = [&](int i) { return (work[i] - work_prev[i]) / swaps; };
            std::fprintf(f, "GPU work per frame: %.0f draws, %.1f resolves, %.1f texture loads (%.0f KB), "
                            "%.1f uploads (%.0f KB), %.0f draws skipped by a test\n",
                         per(0), per(1), per(2), per(3) / 1024.0, per(6), per(7) / 1024.0, per(8));
            std::memcpy(work_prev, work, sizeof work_prev);
          }
          if (const uint64_t* dp = FindStats("tt_depth_pitch")) {  // TEMPORARY test round
            static uint64_t dp_prev[16] = {};
            std::fprintf(f, "depth-only draws per frame by buffer pitch:");
            for (int i = 0; i < 8 && dp[i * 2]; ++i) {
              std::fprintf(f, "  %llu: %.1f", static_cast<unsigned long long>(dp[i * 2]),
                           (dp[i * 2 + 1] - dp_prev[i * 2 + 1]) / swaps);
            }
            std::fprintf(f, "\n");
            std::memcpy(dp_prev, dp, sizeof dp_prev);
          }
          if (tex) {
            auto per = [&](int i) { return (tex[i] - tex_prev[i]) / swaps; };
            std::fprintf(f, "texture loads per frame: %.1f asked, %.1f skipped (bytes unchanged), "
                            "%.1f GPU-written, %.1f MB hashed\n",
                         per(0), per(1), per(2), per(3) / 1048576.0);
            std::memcpy(tex_prev, tex, sizeof tex_prev);
          }
          std::memcpy(frame_prev, frame, sizeof frame_prev);
        }
        if (const uint64_t* hist = FindStats("tt_frame_hist")) {
          const double total_ms = (hist[5] + hist[6] + hist[7] + hist[8] + hist[9]) / 1000.0;
          auto pct = [&](int b) { return total_ms > 0 ? 100.0 * hist[5 + b] / 1000.0 / total_ms : 0.0; };
          std::fprintf(f, "whole session, %llu frames over %.0f:%02.0f of game time: ~60 FPS %.0f%%, "
                          "~45 %.0f%%, ~30 %.0f%%, ~20 %.0f%%, slower %.0f%% of the time; "
                          "%llu spikes of 80 ms or more\n",
                       static_cast<unsigned long long>(hist[11]), std::floor(total_ms / 60000.0),
                       std::fmod(total_ms / 1000.0, 60.0), pct(0), pct(1), pct(2), pct(3), pct(4),
                       static_cast<unsigned long long>(hist[10]));
        }
        std::fprintf(f, "\nbusy threads (CPU %% of one core, last second):\n");
        std::vector<std::pair<double, std::string>> rows;
        for (auto& t : ListTasks()) {
          auto it = cpu_pct.find(t.tid);
          if (it != cpu_pct.end() && it->second >= 1.0) rows.emplace_back(it->second, t.name);
        }
        std::sort(rows.begin(), rows.end(), [](auto& a, auto& b) { return a.first > b.first; });
        for (auto& [pct, name] : rows) std::fprintf(f, "  %5.0f%%  %s\n", pct, name.c_str());
        if (!g_code_dumped && session >= 60.0) {
          g_code_dumped = true;
          DumpHotCode(dir, last);
        }
        WriteWindow(f, "LAST 30 SECONDS", last, std::min<double>(session, kWindowSeconds * kWindowsKept));
        WriteWindow(f, "WHOLE SESSION", total, session);
        std::fclose(f);
        std::rename(tmp.c_str(), (dir + "/profile.txt").c_str());
      }
    }

    next_tick += kTick;
    now = clock::now();
    if (next_tick < now) next_tick = now;
    std::this_thread::sleep_until(next_tick);
  }
}

}  // namespace

void TtSetGuestTranslator(const uint8_t* (*translate)(uint32_t)) { g_translate = translate; }

void TtStartProfiler() {
  const char* dir = std::getenv("TT_PROFILE");
  if (!dir || !*dir) return;
  std::thread(RunProfiler, std::string(dir)).detach();
}

#else
void TtSetGuestTranslator(const uint8_t* (*)(uint32_t)) {}
void TtStartProfiler() {}
#endif
