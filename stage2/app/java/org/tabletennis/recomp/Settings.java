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
    static final String[] FPS_LABELS = {"60 FPS (console)", "40 FPS (120 Hz screen)", "30 FPS", "Uncapped"};
    static final String[] FPS_SHORT = {"60 FPS", "40 FPS", "30 FPS", "Uncapped"};
    static final int[] FPS_VALUES = {60, 40, 30, 0};

    // Visual-risk speed-ups (F10-F12: skip render passes and copies with no visible effect so far). Kept
    // switchable so any venue / character / mode can be compared with the original quickly.
    static final String[] HIDDEN_PASS_LABELS = {"Skipped (faster)", "Drawn (original)"};
    int hiddenPasses = 0;

    // Temporary test row (v100); TT_TEST carries the value to the native side.
    static final String[] TEST_LABELS = {"Off", "Slow image copies"};
    static final String[] TEST_VALUES = {"", "slowcopy"};
    int test = 0;

    private static final String PREFS = "tt_settings";

    int resolution = 0;
    int aa = 1;
    int fps = 0;
    boolean showFps = true;
    int shaderMode = 1;     // 0 = async (draws placeholders while compiling), 1 = wait (no missing objects)
    boolean perfLog = true; // thread monitor + sampling profiler (logs/profile.txt)

    static Settings load(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        Settings s = new Settings();
        s.resolution = clamp(p.getInt("resolution", 0), RESOLUTION_LABELS.length);
        s.aa = clamp(p.getInt("aa", 1), AA_LABELS.length);
        // "fps" was an index into {60, 30, Uncapped} before 40 FPS was added.
        s.fps = p.contains("fps_v2") ? clamp(p.getInt("fps_v2", 0), FPS_LABELS.length)
                : new int[]{0, 2, 3}[clamp(p.getInt("fps", 0), 3)];
        s.showFps = p.getBoolean("show_fps", true);
        s.shaderMode = p.getInt("shader_mode", 1) == 0 ? 0 : 1;
        s.perfLog = p.getBoolean("perf_log", true);
        s.hiddenPasses = clamp(p.getInt("hidden_passes", 0), HIDDEN_PASS_LABELS.length);
        s.test = clamp(p.getInt("test_v100", 0), TEST_LABELS.length);
        return s;
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt("resolution", resolution)
                .putInt("aa", aa)
                .putInt("fps_v2", fps)
                .putBoolean("show_fps", showFps)
                .putInt("shader_mode", shaderMode)
                .putBoolean("perf_log", perfLog)
                .putInt("hidden_passes", hiddenPasses)
                .putInt("test_v100", test)
                .commit();
    }

    /** Every start-screen setting in plain words, for the logs. */
    String describe(String driver) {
        return "Resolution: " + RESOLUTION_LABELS[resolution]
                + " | Anti-aliasing: " + AA_LABELS[aa]
                + " | Frame rate: " + FPS_LABELS[fps]
                + " | Show FPS: " + (showFps ? "On" : "Off")
                + " | Shaders: " + (shaderMode == 0 ? "Async" : "Wait")
                + " | GPU driver: " + driver
                + " | Hidden passes: " + HIDDEN_PASS_LABELS[hiddenPasses]
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
        args.add("--framerate=" + FPS_VALUES[fps]);
        args.add("--show_fps=" + showFps);
        args.add("--fullscreen=true");
        args.add("--async_shader_compilation=" + (shaderMode == 0));
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
