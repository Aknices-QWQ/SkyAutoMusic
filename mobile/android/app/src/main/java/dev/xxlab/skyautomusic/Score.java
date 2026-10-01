package dev.xxlab.skyautomusic;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;

/** A small, JSON-backed score model shared by the offline player and sync client. */
public final class Score {
    public final String filename;
    public final String title;
    public final int bpm;
    public final List<Note> notes;
    public final List<Event> events;
    public final long durationMs;

    public Score(String filename, String title, int bpm, List<Note> notes) {
        this.filename = filename == null ? "score.json" : filename;
        this.title = title == null || title.trim().isEmpty() ? this.filename : title.trim();
        this.bpm = bpm > 0 ? bpm : 120;
        ArrayList<Note> sorted = new ArrayList<>(notes == null ? Collections.<Note>emptyList() : notes);
        sorted.sort(Comparator.comparingLong(note -> note.time));
        this.notes = Collections.unmodifiableList(sorted);
        this.events = Collections.unmodifiableList(groupEvents(sorted));
        this.durationMs = sorted.isEmpty() ? 0L : Math.max(0L, sorted.get(sorted.size() - 1).time - sorted.get(0).time);
    }

    public static Score fromJson(String filename, String json) throws JSONException {
        Object value = new JSONTokener(json == null ? "" : json).nextValue();
        JSONObject source;
        if (value instanceof JSONArray) {
            JSONArray array = (JSONArray) value;
            if (array.length() == 0 || !(array.opt(0) instanceof JSONObject)) {
                throw new JSONException("曲谱数组为空");
            }
            source = array.getJSONObject(0);
        } else if (value instanceof JSONObject) {
            source = (JSONObject) value;
        } else {
            throw new JSONException("曲谱必须是 JSON 对象或数组");
        }

        String safeFilename = filename == null || filename.trim().isEmpty()
                ? source.optString("filename", "score.json") : filename;
        String title = source.optString("title", "");
        if (title.trim().isEmpty()) {
            title = source.optString("songName", "");
        }
        if (title.trim().isEmpty()) {
            title = source.optString("name", "");
        }
        if (title.trim().isEmpty()) {
            title = stem(safeFilename);
        }
        int bpm = source.optInt("bpm", 120);
        JSONArray noteArray = source.optJSONArray("songNotes");
        if (noteArray == null) {
            noteArray = source.optJSONArray("notes");
        }
        if (noteArray == null) {
            throw new JSONException("曲谱缺少 songNotes 或 notes");
        }

        ArrayList<Note> notes = new ArrayList<>();
        for (int i = 0; i < noteArray.length(); i++) {
            JSONObject item = noteArray.optJSONObject(i);
            if (item == null) {
                continue;
            }
            long time = item.optLong("time", -1L);
            String key = item.optString("key", "");
            int index = keyIndex(key);
            if (time < 0 || index < 0 || index > 14) {
                continue;
            }
            notes.add(new Note(time, key, index));
        }
        if (notes.isEmpty()) {
            throw new JSONException("曲谱没有可播放的音符");
        }
        return new Score(safeFilename, title, bpm, notes);
    }

    public String toJson() {
        JSONObject root = new JSONObject();
        JSONArray noteArray = new JSONArray();
        try {
            root.put("filename", filename);
            root.put("songName", title);
            root.put("title", title);
            root.put("bpm", bpm);
            for (Note note : notes) {
                JSONObject item = new JSONObject();
                item.put("time", note.time);
                item.put("key", note.key);
                noteArray.put(item);
            }
            root.put("songNotes", noteArray);
        } catch (JSONException ignored) {
            // JSONObject only rejects unsupported values; all values above are primitives.
        }
        return root.toString();
    }

    private static List<Event> groupEvents(List<Note> notes) {
        LinkedHashMap<Long, ArrayList<Integer>> grouped = new LinkedHashMap<>();
        for (Note note : notes) {
            ArrayList<Integer> keys = grouped.get(note.time);
            if (keys == null) {
                keys = new ArrayList<>();
                grouped.put(note.time, keys);
            }
            if (!keys.contains(note.index)) {
                keys.add(note.index);
            }
        }
        ArrayList<Event> events = new ArrayList<>();
        for (java.util.Map.Entry<Long, ArrayList<Integer>> entry : grouped.entrySet()) {
            events.add(new Event(entry.getKey(), Collections.unmodifiableList(entry.getValue())));
        }
        return events;
    }

    private static int keyIndex(String key) {
        if (key == null) {
            return -1;
        }
        int marker = key.indexOf("Key");
        if (marker < 0) {
            return -1;
        }
        try {
            return Integer.parseInt(key.substring(marker + 3).trim());
        } catch (NumberFormatException ignored) {
            return -1;
        }
    }

    private static String stem(String filename) {
        String value = filename == null ? "score" : filename;
        int slash = Math.max(value.lastIndexOf('/'), value.lastIndexOf('\\'));
        if (slash >= 0) {
            value = value.substring(slash + 1);
        }
        if (value.toLowerCase(Locale.ROOT).endsWith(".json")) {
            value = value.substring(0, value.length() - 5);
        }
        return value.trim().isEmpty() ? "未命名曲谱" : value;
    }

    public static final class Note {
        public final long time;
        public final String key;
        public final int index;

        public Note(long time, String key, int index) {
            this.time = time;
            this.key = key;
            this.index = index;
        }
    }

    public static final class Event {
        public final long time;
        public final List<Integer> keyIndexes;

        public Event(long time, List<Integer> keyIndexes) {
            this.time = time;
            this.keyIndexes = keyIndexes;
        }
    }
}
