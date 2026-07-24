package com.example.aw.collect.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;
import java.util.Map;

/**
 * 视频资产 collect_videos / collect_video_frames。
 */
@Mapper
public interface CollectVideoMapper {

    long countVideosByTask(@Param("taskId") String taskId);

    List<Map<String, Object>> selectVideosByTask(@Param("taskId") String taskId);

    /** 按任务 + 平台查视频（流程图视频子步用） */
    List<Map<String, Object>> selectVideosByTaskAndPlatform(@Param("taskId") String taskId,
                                                            @Param("platform") String platform);

    List<Map<String, Object>> selectFramesByVideoId(@Param("videoId") String videoId);

    /** 按任务删抽帧行（先于 collect_videos） */
    int deleteFramesByTaskId(@Param("taskId") String taskId);

    /** 按任务删视频资产行 */
    int deleteVideosByTaskId(@Param("taskId") String taskId);
}
