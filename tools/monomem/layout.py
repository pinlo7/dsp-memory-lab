"""layout.py — Unity Mono 运行时的版本相关偏移，全部集中在这里。

不同 Unity/Mono 版本这些值可能不同；默认值来自戴森球计划（Unity 2019.4 Mono bleeding edge,
x64）实测。换新游戏时对不上就先校准这里，别去改逻辑代码。
"""
from dataclasses import dataclass, field


@dataclass
class Layout:
    # --- 对象 ---
    obj_vtable: int = 0x00            # 对象头里的 vtable 指针偏移
    # --- vtable -> MonoClass ---
    vtable_class_offsets: tuple = (0x00, 0x08, 0x10)  # 依次尝试
    # --- MonoClass ---
    class_self_ref: int = 0x00        # class+0 == class（身份证）
    class_name_ptr: int = 0x48        # class+0x48 -> char* 类名(UTF8)
    # --- System.String ---
    str_len: int = 0x10               # int32 字符数
    str_chars: int = 0x14             # UTF-16LE 数据
    # --- System.Array (SZArray) ---
    arr_len: int = 0x18               # int64 长度
    arr_data: int = 0x20              # 元素区起点

    # 扫描行为
    chunk_size: int = 4 << 20
    workers: int = 8
    writable_only: bool = True        # 只扫 MEM_PRIVATE 可写（托管堆特征）

    def validate(self):
        assert self.class_name_ptr % 8 == 0
        assert self.str_chars >= self.str_len
        return self


# 常用预设
DSP = Layout()                        # 戴森球计划实测值（即默认值）
