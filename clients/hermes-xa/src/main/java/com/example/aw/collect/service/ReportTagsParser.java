package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 解析 hermes_user_dialogues.report_tags（JSON 数组字符串）。
 */
final class ReportTagsParser {

    private ReportTagsParser() {
    }

    static List<String> parse(Object raw) {
        if (raw == null) {
            return Collections.emptyList();
        }
        String text = String.valueOf(raw).trim();
        if (text.isEmpty() || "null".equalsIgnoreCase(text)) {
            return Collections.emptyList();
        }
        try {
            Object parsed = JSON.parse(text);
            if (!(parsed instanceof List)) {
                return Collections.emptyList();
            }
            List<?> src = (List<?>) parsed;
            List<String> out = new ArrayList<String>(src.size());
            for (Object item : src) {
                if (item == null) {
                    continue;
                }
                String tag = String.valueOf(item).trim();
                if (!tag.isEmpty()) {
                    out.add(tag);
                }
            }
            return out;
        } catch (Exception ignore) {
            return Collections.emptyList();
        }
    }
}
