package org.tabletennis.recomp;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;

import java.util.Random;

/**
 * A small Pong that plays itself on the start screen, turned vertical (paddles at the top and
 * bottom). Ported from the owner's canvas Pong (github.com/henroids/not_portfolio, js/script.js):
 * same rules (ball speeds up 5% per hit, the paddle's motion bends the ball, a missed ball
 * scores for the other side and is served again from the centre) and the same 3x5 pixel
 * digits, but both paddles are computer players and everything is drawn on a coarse pixel grid
 * in the start screen's colours.
 */
final class PongView extends View {
    // 3x5 digits, row by row (from the original).
    private static final String[] DIGITS = {
            "111101101101111", "010010010010010", "111001111100111", "111001111001111",
            "101101111001001", "111100111001111", "111100111101111", "111001001001001",
            "111101111101111", "111101111001111"};

    private final Paint white = new Paint(), grey = new Paint(), dim = new Paint();
    private final Random random = new Random();
    private final float cell;  // one game pixel, in screen pixels

    // Court in game pixels; positions are centres.
    private float w, h;
    private final float paddleW = 10, paddleH = 2, ball = 2;
    private final float[] paddleX = new float[2], paddleVx = new float[2], aimError = new float[2];
    private float bx, by, vx, vy, serveIn;
    private final int[] score = new int[2];
    private long lastNanos;
    private boolean running;

    PongView(Context context, int whiteColor, int greyColor, int dimColor) {
        super(context);
        cell = Math.max(2, Math.round(3 * context.getResources().getDisplayMetrics().density));
        white.setColor(whiteColor);
        grey.setColor(greyColor);
        dim.setColor(dimColor);
        setFocusable(false);
    }

    void setRunning(boolean run) {
        if (running == run) return;
        running = run;
        lastNanos = 0;
        if (run) postInvalidateOnAnimation();
    }

    @Override protected void onSizeChanged(int width, int height, int oldW, int oldH) {
        w = (float) Math.floor(width / cell);
        h = (float) Math.floor(height / cell);
        paddleX[0] = paddleX[1] = w / 2;
        reset();
    }

    private void reset() {
        bx = w / 2;
        by = h / 2;
        vx = vy = 0;
        serveIn = 0.8f;
    }

    private void serve() {
        float speed = h * 0.45f;  // court lengths per second, like the original's 250 px/s
        vy = speed * (random.nextBoolean() ? 1 : -1);
        vx = speed * (random.nextFloat() * 1.2f - 0.6f);
        scaleTo(speed);
        aimError[0] = aimError[1] = 0;
    }

    private void scaleTo(float speed) {
        float len = (float) Math.hypot(vx, vy);
        if (len > 0) { vx *= speed / len; vy *= speed / len; }
    }

    private void update(float dt) {
        if (w <= 0) return;
        if (vx == 0 && vy == 0) {
            if ((serveIn -= dt) <= 0) serve();
        } else {
            bx += vx * dt;
            by += vy * dt;
            if ((vx < 0 && bx - ball / 2 < 0) || (vx > 0 && bx + ball / 2 > w)) vx = -vx;
            if (by < -ball || by > h + ball) {
                score[by < 0 ? 1 : 0]++;
                if (score[0] > 99 || score[1] > 99) score[0] = score[1] = 0;
                reset();
            }
        }
        // Computer paddles: follow the ball when it's coming their way (with a small aiming
        // error picked per rally, so they miss now and then), drift to the middle otherwise.
        for (int p = 0; p < 2; p++) {
            boolean coming = p == 0 ? vy < 0 : vy > 0;
            if (coming && aimError[p] == 0) aimError[p] = (random.nextFloat() * 2 - 1) * paddleW * 0.75f;
            if (!coming) aimError[p] = 0;
            float target = coming ? bx + aimError[p] : w / 2;
            float maxSpeed = w * 1.1f;
            float move = Math.max(-maxSpeed * dt, Math.min(maxSpeed * dt, target - paddleX[p]));
            float before = paddleX[p];
            paddleX[p] = Math.max(paddleW / 2, Math.min(w - paddleW / 2, paddleX[p] + move));
            paddleVx[p] = dt > 0 ? (paddleX[p] - before) / dt : 0;
        }
        collide(0, 2 + paddleH / 2, vy < 0);
        collide(1, h - 2 - paddleH / 2, vy > 0);
    }

    private void collide(int p, float py, boolean coming) {
        if (!coming) return;
        if (Math.abs(bx - paddleX[p]) < (paddleW + ball) / 2 && Math.abs(by - py) < (paddleH + ball) / 2) {
            float speed = (float) Math.hypot(vx, vy) * 1.05f;
            vy = -vy;
            vx += paddleVx[p] * 0.2f;
            scaleTo(Math.min(speed, h * 1.6f));
            by = py + (p == 0 ? 1 : -1) * (paddleH + ball) / 2;
        }
    }

    @Override protected void onDraw(Canvas c) {
        long now = System.nanoTime();
        if (running && lastNanos != 0) update(Math.min(0.05f, (now - lastNanos) / 1e9f));
        lastNanos = now;

        // Net: a dashed line across the middle.
        float mid = (float) Math.floor(h / 2);
        for (float x = 0; x < w; x += 4) block(c, dim, x, mid, 2, 1);
        // Scores at the left, each in its own half, like the original's.
        digits(c, score[0], 2, mid - 8);
        digits(c, score[1], 2, mid + 3);
        // Paddles and ball.
        block(c, white, paddleX[0] - paddleW / 2, 2, paddleW, paddleH);
        block(c, white, paddleX[1] - paddleW / 2, h - 2 - paddleH, paddleW, paddleH);
        if (vx != 0 || vy != 0 || ((int) (serveIn * 4)) % 2 == 0) {
            block(c, white, bx - ball / 2, by - ball / 2, ball, ball);
        }
        if (running) postInvalidateOnAnimation();
    }

    private void digits(Canvas c, int value, float x, float y) {
        String s = Integer.toString(value);
        for (int i = 0; i < s.length(); i++) {
            String bits = DIGITS[s.charAt(i) - '0'];
            for (int k = 0; k < 15; k++) {
                if (bits.charAt(k) == '1') block(c, grey, x + i * 4 + k % 3, y + k / 3, 1, 1);
            }
        }
    }

    /** Fills whole game pixels, so every edge lands on the pixel grid. */
    private void block(Canvas c, Paint paint, float x, float y, float bw, float bh) {
        float left = Math.round(x) * cell, top = Math.round(y) * cell;
        c.drawRect(left, top, left + Math.round(bw) * cell, top + Math.round(bh) * cell, paint);
    }
}
