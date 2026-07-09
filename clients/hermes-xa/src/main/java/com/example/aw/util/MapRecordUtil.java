package com.example.aw.util;

import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

/**
 * 将 MyBatis Map 结果中的日期类型转为字符串，避免 GraphQL Jackson 序列化失败
 */
public final class MapRecordUtil {

    private static final DateTimeFormatter DATETIME_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private MapRecordUtil() {
    }

    public static void normalizeRecords(List<Map<String, Object>> records) {
        if (records == null || records.isEmpty()) {
            return;
        }
        for (Map<String, Object> record : records) {
            normalizeRecord(record);
        }
    }

    public static void normalizeRecord(Map<String, Object> record) {
        if (record == null || record.isEmpty()) {
            return;
        }
        for (Map.Entry<String, Object> entry : record.entrySet()) {
            Object value = entry.getValue();
            if (value instanceof LocalDateTime) {
                entry.setValue(((LocalDateTime) value).format(DATETIME_FMT));
            } else if (value instanceof LocalDate) {
                entry.setValue(value.toString());
            } else if (value instanceof Timestamp) {
                entry.setValue(((Timestamp) value).toLocalDateTime().format(DATETIME_FMT));
            } else if (value instanceof java.util.Date) {
                entry.setValue(new Timestamp(((java.util.Date) value).getTime()).toLocalDateTime().format(DATETIME_FMT));
            }
        }
    }
}
