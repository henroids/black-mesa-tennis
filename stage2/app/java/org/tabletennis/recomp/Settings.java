package org.tabletennis.recomp;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.List;

/** Launcher settings, stored in SharedPreferences and turned into ReXGlue cvar arguments. */
final class Settings {
    static final String[] RESOLUTION_LABELS = {
            "720p (console native)",
            "1440p (2x, sharpest on the 1080p screen)"};
    static final String[] AA_LABELS = {"Off", "FXAA", "FXAA (extreme)"};
    static final String[] AA_VALUES = {"none", "fxaa", "fxaa_extreme"};
    // The game always runs capped at its own 60 FPS, and shaders are always prepared completely
    // before first use ("Wait": no missing objects). Both used to be options.
    static final int FPS = 60;

    // Visual-risk speed-ups (F10-F12 and F15-F18: skipped passes, direct image copies; no visible
    // effect so far). Kept switchable so any venue / character / mode can be compared with the
    // original quickly.
    static final String[] HIDDEN_PASS_LABELS = {"Skipped (faster)", "Drawn (original)"};
    int hiddenPasses = 0;

    // Branch `renderer` only: which renderer draws the game (RENDERER.md).
    static final String[] RENDERER_LABELS = {"Old", "New"};
    int renderer = 0;

    // Temporary test row (T3, renderer branch): hide one heavy shader, or turn its alpha test off.
    static final String[] TEST_LABELS = {"Off", "Hide shader A", "A without alpha test",
            "Hide shader B", "Hide shader C"};
    static final String[] TEST_VALUES = {"", "hide_5c7a", "noalpha_5c7a", "hide_2cf2", "hide_8a14"};
    int test = 0;

    private static final String PREFS = "tt_settings";

    int resolution = 0;
    int aa = 1;
    boolean showFps = true;
    boolean perfLog = true; // thread monitor + sampling profiler (logs/profile.txt)

    static Settings load(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        Settings s = new Settings();
        s.resolution = clamp(p.getInt("resolution", 0), RESOLUTION_LABELS.length);
        s.aa = clamp(p.getInt("aa", 1), AA_LABELS.length);
        s.showFps = p.getBoolean("show_fps", true);
        s.perfLog = p.getBoolean("perf_log", true);
        s.hiddenPasses = clamp(p.getInt("hidden_passes", 0), HIDDEN_PASS_LABELS.length);
        s.renderer = clamp(p.getInt("renderer", 0), RENDERER_LABELS.length);
        s.test = clamp(p.getInt("test", 0), TEST_LABELS.length);
        return s;
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt("resolution", resolution)
                .putInt("aa", aa)
                .putBoolean("show_fps", showFps)
                .putBoolean("perf_log", perfLog)
                .putInt("hidden_passes", hiddenPasses)
                .putInt("renderer", renderer)
                .putInt("test", test)
                .commit();
    }

    /** Every start-screen setting in plain words, for the logs. */
    String describe(String driver) {
        return "Resolution: " + RESOLUTION_LABELS[resolution]
                + " | Anti-aliasing: " + AA_LABELS[aa]
                + " | Show FPS: " + (showFps ? "On" : "Off")
                + " | GPU driver: " + driver
                + " | Hidden passes: " + HIDDEN_PASS_LABELS[hiddenPasses]
                + " | Renderer: " + RENDERER_LABELS[renderer]
                + " | Test: " + TEST_LABELS[test]
                + " | Performance log: " + (perfLog ? "On" : "Off");
    }

    /** Command-line cvars for the native runtime. */
    List<String> toArguments(String isoPath) {
        List<String> args = new ArrayList<>();
        if (isoPath != null) args.add("--game_data_root=" + isoPath);
        int scale = resolution == 1 ? 2 : 1;
        // Guest video mode: what the game is told the TV is. Table Tennis is a 720p title.
        args.add("--video_mode_width=1280");
        args.add("--video_mode_height=720");
        // Host-side internal render scale (1 = native, 2 = 2x each axis).
        args.add("--resolution_scale=" + scale);
        args.add("--draw_resolution_scale_x=" + scale);
        args.add("--draw_resolution_scale_y=" + scale);
        args.add("--swap_post_effect=" + AA_VALUES[aa]);
        args.add("--framerate=" + FPS);
        args.add("--show_fps=" + showFps);
        args.add("--fullscreen=true");
        args.add("--async_shader_compilation=false");
        // Shader (pipeline) preparation threads: only matters on first visits, fixed at 4.
        args.add("--vulkan_pipeline_creation_threads=4");
        args.add("--protect_zero=false");
        args.add("--gpu_allow_invalid_fetch_constants=true");
        // Present from the UI thread: otherwise the GPU command thread painted and waited
        // for the display's next refresh on every guest frame.
        args.add("--host_present_from_non_ui_thread=false");
        return args;
    }

    private static int clamp(int value, int count) {
        return value < 0 || value >= count ? 0 : value;
    }
}
