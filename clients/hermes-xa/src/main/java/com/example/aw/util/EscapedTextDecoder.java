package com.example.aw.util;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 解码字面量 \\uXXXX / \\n（部分爬虫把 Unicode 转义当明文返回）。
 */
public final class EscapedTextDecoder {

    private static final Pattern UNICODE_ESC = Pattern.compile("\\\\u([0-9a-fA-F]{4})");

    private EscapedTextDecoder() {
    }

    public static String decode(String value) {
        if (value == null || value.indexOf('\\') < 0) {
            return value;
        }
        if (!UNICODE_ESC.matcher(value).find()) {
            return value;
        }
        Matcher m = UNICODE_ESC.matcher(value);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            char ch = (char) Integer.parseInt(m.group(1), 16);
            m.appendReplacement(sb, Matcher.quoteReplacement(String.valueOf(ch)));
        }
        m.appendTail(sb);
        String out = sb.toString();
        return out.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace("\\\"", "\"")
                .replace("\\\\", "\\");
    }

    /**
     * 对 [{label,value}, ...] 中的 value 做解码；其它结构原样返回。
     */
    @SuppressWarnings("unchecked")
    public static Object decodeDisplayFields(Object fields) {
        if (!(fields instanceof List)) {
            return fields;
        }
        List<?> list = (List<?>) fields;
        List<Object> out = new ArrayList<Object>(list.size());
        for (Object item : list) {
            if (!(item instanceof Map)) {
                out.add(item);
                continue;
            }
            Map<String, Object> map = new LinkedHashMap<String, Object>((Map<String, Object>) item);
            Object v = map.get("value");
            if (v instanceof String) {
                map.put("value", decode((String) v));
            }
            out.add(map);
        }
        return out;
    }
}
