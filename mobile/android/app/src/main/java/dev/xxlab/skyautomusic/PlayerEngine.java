package dev.xxlab.skyautomusic;

import java.util.List;

/** Timing loop for offline playback. It never talks to the desktop process. */
public final class PlayerEngine {
    public interface Listener {
        void onStatus(String text);

        void onProgress(int percent, long elapsedMs, String countText);

        void onFinished(boolean stopped);

        void onError(String message);
    }

    private final Object lock = new Object();
    private volatile boolean stopRequested;
    private volatile boolean running;
    private Thread worker;

    public boolean isRunning() {
        return running;
    }

    public void start(final Score score, final float speed, final float[] xs, final float[] ys,
                      final int startPercent, final Listener listener) {
        if (score == null || score.events.isEmpty() || xs == null || ys == null) {
            if (listener != null) {
                listener.onError("曲谱没有可播放的音符");
            }
            return;
        }
        synchronized (lock) {
            if (running) {
                return;
            }
            stopRequested = false;
            running = true;
            worker = new Thread(() -> play(score, speed, xs.clone(), ys.clone(), startPercent, listener),
                    "SkyAutoMusic-MobilePlayer");
            worker.start();
        }
    }

    public void stop() {
        Thread thread;
        synchronized (lock) {
            if (!running) {
                return;
            }
            stopRequested = true;
            thread = worker;
        }
        if (thread != null) {
            thread.interrupt();
        }
    }

    private void play(Score score, float requestedSpeed, float[] xs, float[] ys,
                      int startPercent, Listener listener) {
        boolean stopped = false;
        try {
            if (!SkyAccessibilityService.isConnected()) {
                notifyError(listener, "请先在系统设置中启用 SkyAutoMusic 无障碍服务");
                stopped = true;
                return;
            }
            float speed = Math.max(0.25f, Math.min(2.0f, requestedSpeed));
            List<Score.Event> events = score.events;
            long firstTime = events.get(0).time;
            int startIndex = indexForPercent(events, startPercent);
            for (int remaining = 3; remaining > 0; remaining--) {
                if (stopRequested) {
                    stopped = true;
                    return;
                }
                notifyStatus(listener, remaining + " 秒后开始，请切换到目标游戏");
                if (!sleepUntil(System.nanoTime() + 1_000_000_000L)) {
                    stopped = true;
                    return;
                }
            }
            long wallStart = System.nanoTime();
            long baseScoreTime = events.get(startIndex).time;
            notifyStatus(listener, "演奏中：" + score.title);
            for (int index = startIndex; index < events.size(); index++) {
                if (stopRequested) {
                    stopped = true;
                    break;
                }
                Score.Event event = events.get(index);
                long relativeMs = Math.max(0L, Math.round((event.time - baseScoreTime) / speed));
                if (!sleepUntil(wallStart + relativeMs * 1_000_000L)) {
                    stopped = true;
                    break;
                }
                if (!SkyAccessibilityService.dispatchChord(event.keyIndexes, xs, ys)) {
                    notifyError(listener, "无障碍服务已断开，请重新启用后重试");
                    stopped = true;
                    break;
                }
                long elapsed = Math.max(0L, Math.round((event.time - firstTime) / speed));
                int percent = score.durationMs == 0 ? 100
                        : (int) Math.round((event.time - firstTime) * 100.0 / score.durationMs);
                percent = Math.max(0, Math.min(100, percent));
                notifyProgress(listener, percent, elapsed,
                        (index + 1) + "/" + events.size());
            }
        } catch (Throwable error) {
            if (!stopRequested) {
                notifyError(listener, error.getMessage() == null ? "播放失败" : error.getMessage());
            }
            stopped = true;
        } finally {
            synchronized (lock) {
                running = false;
                worker = null;
            }
            if (listener != null) {
                listener.onFinished(stopped || stopRequested);
            }
        }
    }

    private boolean sleepUntil(long targetNanos) {
        while (!stopRequested) {
            long remaining = targetNanos - System.nanoTime();
            if (remaining <= 0) {
                return true;
            }
            try {
                long millis = Math.min(30L, Math.max(1L, remaining / 1_000_000L));
                Thread.sleep(millis);
            } catch (InterruptedException ignored) {
                // stop() uses interrupt to wake this loop immediately.
            }
        }
        return false;
    }

    private static int indexForPercent(List<Score.Event> events, int value) {
        int percent = Math.max(0, Math.min(100, value));
        if (percent == 0 || events.size() == 1) {
            return 0;
        }
        long first = events.get(0).time;
        long target = first + Math.round((events.get(events.size() - 1).time - first) * percent / 100.0);
        for (int index = 0; index < events.size(); index++) {
            if (events.get(index).time >= target) {
                return index;
            }
        }
        return events.size() - 1;
    }

    private static void notifyStatus(Listener listener, String text) {
        if (listener != null) {
            listener.onStatus(text);
        }
    }

    private static void notifyProgress(Listener listener, int percent, long elapsed, String count) {
        if (listener != null) {
            listener.onProgress(percent, elapsed, count);
        }
    }

    private static void notifyError(Listener listener, String message) {
        if (listener != null) {
            listener.onError(message);
        }
    }
}
