package com.example.aw.collect.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;
import java.util.Map;

/**
 * 图片资产表 collect_images 查询。
 */
@Mapper
public interface CollectImageMapper {

    /**
     * 按条件统计任务下图片数量。
     */
    long countByTask(@Param("taskId") String taskId,
                     @Param("sourceType") String sourceType,
                     @Param("analyzeStatus") String analyzeStatus,
                     @Param("storageStatus") String storageStatus);

    /**
     * 分页查询任务下图片列表。
     */
    List<Map<String, Object>> selectPageByTask(@Param("taskId") String taskId,
                                               @Param("sourceType") String sourceType,
                                               @Param("analyzeStatus") String analyzeStatus,
                                               @Param("storageStatus") String storageStatus,
                                               @Param("offset") int offset,
                                               @Param("limit") int limit);

    /**
     * 按 imageId 查询单条完整记录。
     */
    Map<String, Object> selectByImageId(@Param("imageId") String imageId);
}
