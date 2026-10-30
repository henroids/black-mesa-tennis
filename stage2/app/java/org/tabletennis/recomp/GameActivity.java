package org.tabletennis.recomp;

import android.os.Bundle;
import android.system.ErrnoException;
import android.system.Os;
import android.util.Log;
import android.view.WindowManager;

import java.io.File;
import java.util.List;

import org.libsdl.app.SDLActivity;

/** SDL activity that hosts the recompiled game (libmain.so). */
public final class GameActivity extends SDLActivity {
    private static final String TAG = "TableTennis";

    @Override protected void onCreate(Bundle savedInstanceState) {
        // Everything the native side reads from the environment must be set before
        // SDLActivity.onCreate loads libmain.so.
        Settings settings = Settings.load(this);
        File base = GameFiles.baseDir(this);
        setEnv("XDG_DATA_HOME", new File(base, "xdg").getAbsolutePath());
        setEnv("HOME", base.getAbsolutePath());
        String tu = settings.turnipDebug();
        if (!tu.isEmpty()) setEnv("TU_DEBUG", tu);
        if (settings.experiment == Settings.EXPERIMENT_STRICT_FETCH) setEnv("TT_STRICT_FETCH", "1");
        if (settings.dumpShaders) new File(settings.shaderDumpPath).mkdirs();
        if (!settings.fastWatch) setEnv("TT_SLOW_WATCH", "1");
        if (settings.framesInFlight() != null) setEnv("TT_FRAMES_IN_FLIGHT", settings.framesInFlight());
        if (settings.uiUncapped()) setEnv("TT_UI_UNCAPPED", "1");
        setEnv("TT_GAME_MSAA", settings.gameMsaa == 0 ? "off" : "on");
        if (settings.uiHz() != null) setEnv("TT_UI_HZ", settings.uiHz());
        if (settings.perfLog) {
            // Native sampling profiler: writes logs/profile.txt every 10 seconds.
            File logs = GameFiles.logsDir(this);
            logs.mkdirs();
            setEnv("TT_PROFILE", logs.getAbsolutePath());
            copySymbolLists(new File(logs, "syms"));
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        super.onCreate(savedInstanceState);
        if (settings.perfLog) {
            GameFiles.logsDir(this).mkdirs();
            monitor = new ThreadMonitor(GameFiles.logsDir(this));
            monitor.start();
        }
    }

    private ThreadMonitor monitor;

    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemBars();
            // SDL can re-apply its own window flags after focus changes; hide again shortly after.
            getWindow().getDecorView().postDelayed(this::hideSystemBars, 600);
        }
    }

    @Override protected void onResume() {
        super.onResume();
        hideSystemBars();
    }

    /** Hide the status bar and the gesture/navigation bar while the game runs. */
    private void hideSystemBars() {
        if (android.os.Build.VERSION.SDK_INT < 30) return;
        android.view.WindowInsetsController c = getWindow().getInsetsController();
        if (c == null) return;
        c.hide(android.view.WindowInsets.Type.systemBars());
        c.setSystemBarsBehavior(android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
    }

    // ------------------------------------------------------------ return to start screen

    private boolean selectHeld, startHeld;
    private android.app.AlertDialog quitDialog;

    @Override public boolean dispatchKeyEvent(android.view.KeyEvent event) {
        int code = event.getKeyCode();
        boolean down = event.getAction() == android.view.KeyEvent.ACTION_DOWN;
        if (code == android.view.KeyEvent.KEYCODE_BUTTON_SELECT) selectHeld = down;
        if (code == android.view.KeyEvent.KEYCODE_BUTTON_START) startHeld = down;
        if (quitDialog == null || !quitDialog.isShowing()) {
            if (down && selectHeld && startHeld) {
                selectHeld = startHeld = false;
                showQuitDialog();
                return true;
            }
            if (code == android.view.KeyEvent.KEYCODE_BACK) {
                if (down && event.getRepeatCount() == 0) showQuitDialog();
                return true;
            }
        }
        return super.dispatchKeyEvent(event);
    }

    @Override public void onBackPressed() { showQuitDialog(); }

    private void showQuitDialog() {
        if (quitDialog != null && quitDialog.isShowing()) return;
        quitDialog = new android.app.AlertDialog.Builder(this,
                android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Return to start screen?")
                .setMessage("The game will close. Unsaved progress in the current match is lost.")
                .setPositiveButton("Return", (d, w) -> returnToLauncher())
                .setNegativeButton("Keep playing", (d, w) -> d.dismiss())
                .setOnDismissListener(d -> hideSystemBars())
                .create();
        quitDialog.show();
        android.widget.Button keep = quitDialog.getButton(android.content.DialogInterface.BUTTON_NEGATIVE);
        if (keep != null) keep.requestFocus();
    }

    private void returnToLauncher() {
        android.content.Intent home = new android.content.Intent(this, LauncherActivity.class)
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK
                        | android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP);
        // Clear the "custom driver in use" marker first: the runtime's own shutdown can
        // abort while closing, which made the launcher think the driver had crashed and
        // silently switch back to the system driver.
        new File(DriverManager.root(this), "custom-driver-starting").delete();
        if (monitor != null) monitor.shutdown();
        startActivity(home);
        // End the game process right away instead of running the native shutdown.
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    @Override protected String[] getLibraries() {
        // SDL3 is linked statically into the runtime, so only the game library is loaded here.
        return new String[]{"main"};
    }

    @Override protected String[] getArguments() {
        File iso = GameFiles.findIso(this);
        List<String> args = Settings.load(this).toArguments(iso == null ? null : iso.getAbsolutePath());
        Log.i(TAG, "Launching with " + args);
        return args.toArray(new String[0]);
    }

    @Override protected void onDestroy() {
        if (monitor != null) monitor.shutdown();
        super.onDestroy();
        // The game runs in its own ":game" process. End it when the game closes so the
        // next launch starts clean (fresh runtime, freshly chosen GPU driver).
        if (isFinishing()) {
            // Normal exit: clear the "custom driver in use" marker. killProcess skips the
            // native atexit handler that would otherwise remove it, which made the launcher
            // think every session had crashed and fall back to the system driver.
            new File(DriverManager.root(this), "custom-driver-starting").delete();
            android.os.Process.killProcess(android.os.Process.myPid());
        }
    }

    /** Copies the runtime libraries' symbol lists (assets/syms) next to the logs once, so an
     *  exported log zip can be symbolized. SDK code only; no game code. */
    private void copySymbolLists(File dir) {
        try {
            String[] names = getAssets().list("syms");
            if (names == null) return;
            dir.mkdirs();
            for (String name : names) {
                File out = new File(dir, name);
                if (out.isFile() && out.length() > 0) continue;
                try (java.io.InputStream in = getAssets().open("syms/" + name);
                     java.io.OutputStream o = new java.io.FileOutputStream(out)) {
                    byte[] buf = new byte[1 << 16];
                    int n;
                    while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
                }
            }
        } catch (java.io.IOException e) {
            Log.w(TAG, "symbol lists not copied", e);
        }
    }

    private static void setEnv(String name, String value) {
        try {
            Os.setenv(name, value, true);
        } catch (ErrnoException e) {
            Log.w(TAG, "setenv " + name + " failed", e);
        }
    }
}
