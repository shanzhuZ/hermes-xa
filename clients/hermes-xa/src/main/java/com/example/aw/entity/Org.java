package com.example.aw.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * @Author Mlz.
 * @Date 2021/4/28 - 16:24
 */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class Org implements Serializable {
    private String tableName;
    private String columnFamily;
    private int version;
}
