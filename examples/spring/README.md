# 单自由度弹簧离线示例

本例验证桌面参数编辑、具名外接文件生成、版本匹配、独立运行、检查和曲线。
它是简单弹簧计算，不是电池模型或PRIME单元的物理验证。

`u` 为弹簧伸长，属于这个示例的主变量；`k` 为正弹簧刚度。
弹性储能为 `E = k u²/2`，力为 `f = k u`，切线为 `df/du = k`。
本例只含弹性储能，不含耗散、反应、扩散、温度或老化。

窗口中的位移单位为mm，计算力和能量前转为m。
位移在给定时间内从初值线性增加至终值；该时间只是载荷参数，不表示动力学积分。
`driver.py` 和 `spring.f90` 使用相同算法。二者分别用于Python参考运行和真实ifort独立编译运行。
Fortran程序是独立program，不是Abaqus的UEL入口；`template.inp` 不提交求解。

Fortran配置通过 `compile_spring.py` 调用已安装的Intel/MSVC工具链。
它优先只读发现现有Intel、MSVC及Windows SDK的已安装布局；找不到时再查找当前ifort附近
的环境脚本，也允许用 `PRIME_INTEL_ENV` 显式指定；
不安装编译器、不持久化或打印用户环境变量。该配置使用Windows ifort，未安装时会明确失败。

参数通过新副本的 `parameters.dat` 提供，源文件不需要随参数修改。
输出 `history.csv`：时间、位移、力、弹性能；`metrics.csv`：力对位移导数的中心差分相对误差。
