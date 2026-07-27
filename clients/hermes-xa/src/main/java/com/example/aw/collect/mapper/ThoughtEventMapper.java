package com.example.aw.collect.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;
import java.util.Map;

/**
 * 思考流精简事件 hermes_thought_events（仅 _thinking 整句）。
 */
@Mapper
public interface ThoughtEventMapper {

    int insertIgnore(@Param("taskId") String taskId,
                     @Param("seq") int seq,
                     @Param("eventType") String eventType,
                     @Param("toolName") String toolName,
                     @Param("content") String content);

    List<Map<String, Object>> selectAfterSeq(@Param("taskId") String taskId,
                                             @Param("afterSeq") long afterSeq,
                                             @Param("limit") int limit);

    Integer selectMaxSeq(@Param("taskId") String taskId);

    int deleteByTaskId(@Param("taskId") String taskId);
}
