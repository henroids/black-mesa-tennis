// rexgpu-tt: this project's own renderer plug-in for Rockstar Table Tennis (branch `renderer`).
//
// Loaded through the ReXGlue SDK's GPU plug-in seam when the start screen's
// "Renderer" row is set to "New". Step C2 (this file): the plug-in only proves
// the plumbing. It loads the SDK's Xenos renderer and forwards every call to it,
// so the picture and speed must be identical to "Old". Later steps replace the
// forwarded parts one at a time. See RENDERER.md.
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <cstdint>
#include <filesystem>
#include <memory>
#include <utility>

#include <rex/logging.h>
#include <rex/system/gpu_plugin.h>

namespace {

using rex::system::IGraphicsSystem;

class TtGraphicsSystem final : public IGraphicsSystem {
 public:
  explicit TtGraphicsSystem(std::unique_ptr<IGraphicsSystem> inner) : inner_(std::move(inner)) {}

  rex::X_STATUS SetupPresentation(rex::ui::WindowedAppContext* app_context) override {
    return inner_->SetupPresentation(app_context);
  }
  rex::X_STATUS SetupGuestGpu(rex::runtime::FunctionDispatcher* function_dispatcher,
                         rex::system::KernelState* kernel_state) override {
    rex::X_STATUS status = inner_->SetupGuestGpu(function_dispatcher, kernel_state);
    REXLOG_INFO("tt-renderer: guest GPU set up through the new plug-in (status {:08X})",
                static_cast<uint32_t>(status));
    return status;
  }
  bool has_presentation() const override { return inner_->has_presentation(); }
  rex::ui::GraphicsProvider* provider() const override { return inner_->provider(); }
  rex::ui::Presenter* presenter() const override { return inner_->presenter(); }
  void SetInterruptCallback(uint32_t callback, uint32_t user_data) override {
    inner_->SetInterruptCallback(callback, user_data);
  }
  void InitializeRingBuffer(uint32_t ptr, uint32_t size_log2) override {
    inner_->InitializeRingBuffer(ptr, size_log2);
  }
  void EnableReadPointerWriteBack(uint32_t ptr, uint32_t block_size_log2) override {
    inner_->EnableReadPointerWriteBack(ptr, block_size_log2);
  }
  void InitializeShaderStorage(const std::filesystem::path& cache_root, uint32_t title_id,
                               bool blocking) override {
    inner_->InitializeShaderStorage(cache_root, title_id, blocking);
  }
  void Shutdown() override { inner_->Shutdown(); }

 private:
  std::unique_ptr<IGraphicsSystem> inner_;
};

}  // namespace

extern "C" REX_GPU_PLUGIN_EXPORT uint32_t rex_gpu_abi_version(void) {
  return rex::system::kGpuPluginAbiVersion;
}

extern "C" REX_GPU_PLUGIN_EXPORT rex::system::IGraphicsSystem* rex_gpu_create(
    uint32_t abi_version, const rex::system::GpuCreateInfo* info) {
  if (abi_version != rex::system::kGpuPluginAbiVersion || !info ||
      info->struct_size < sizeof(rex::system::GpuCreateInfo)) {
    REXLOG_ERROR("tt-renderer: plug-in ABI mismatch");
    return nullptr;
  }
  auto inner = rex::system::LoadGpuPlugin("xenos", info->backend ? info->backend : "any");
  if (!inner) {
    REXLOG_ERROR("tt-renderer: the SDK renderer could not be loaded");
    return nullptr;
  }
  REXLOG_INFO("tt-renderer: new renderer plug-in loaded (step C2: forwards to the SDK renderer)");
  return new TtGraphicsSystem(std::move(inner));
}
