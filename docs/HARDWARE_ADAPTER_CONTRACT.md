# Hardware Adapter Contract（软件侧冻结草案）

这份文件只冻结软件与未来控制桥之间的边界，不替硬件组决定 Writer、Lock、Reset 或串口时序。

## 软件保证提供

每次发布一个完整帧：

```json
{
  "protocol": "touchsight-matrix-v1",
    "device_id": "touchsight_edu_hd_v1",
  "frame": {
    "version": 1,
    "frame_id": 12,
    "cols": 40,
    "rows": 50,
    "levels": 4,
    "orientation": "row-major; origin top-left; x right; y down",
    "height_mm": [0, 2, 4, 6],
    "height_rows": [[0, 1, 2, 3, "...共40项"], "...共50行"]
  }
}
```

约束：

- 每帧永远是完整 50 行 × 40 列，共 2000 点；
- 每个值只能是 `0/1/2/3`；
- `0/1/2/3` 分别表示 H0/H1/H2/H3；
- 左上角为 `(row=0, col=0)`；
- `frame_id` 单调递增；
- 软件只描述目标表面，不下发未冻结的机械动作序列。

## 当前可用出口

1. `output/hardware/current_frame.json`：便于 ESP32/RDK 控制桥开发和调试；
2. `output/hardware/current_frame.tse`：软件内部的紧凑 2-bit 帧，含 CRC32；
3. `TOUCHSIGHT_HARDWARE_URL`：配置后，以 HTTP POST 发送同一 JSON。

`.tse` 当前只作为软件侧稳定交换格式，不等同于已经冻结的 ESP32 串口协议。

## 控制桥应负责

```text
收到完整目标矩阵
→ 校验尺寸、值域、frame_id
→ RELEASE / Reset 到 H0（如果机械流程需要）
→ 按 Writer 并行度规划扫描
→ 写入每个 H1/H2/H3
→ LOCK
→ 返回成功、失败位置与耗时
```

## 等待硬件组确认

- ESP32 接收通道：USB Serial、Wi-Fi HTTP、UART 或其他；
- 40 列和 50 行是否与机械坐标完全一致，是否需要翻转/转置；
- 矩阵值是否直接使用 `0/1/2/3`；
- 每帧前是否必须全局 Reset；
- Writer 并行头数及扫描顺序；
- Lock/Release/Reset 的状态机和超时；
- 写入完成 ACK、错误码和重试规则；
- 预计完整刷新时间。
