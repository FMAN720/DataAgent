# 业务口径说明

本文档定义电商分析库中的指标口径。回答涉及下列术语时必须遵守这里的定义。
每个 ## 小节会被切成一条检索记录。

## 通用约定

- 金额字段（total_amount、discount_amount、pay_amount、item_amount、refund_amount、list_price、cost_price）单位都是「分」，对外展示时除以 100 换算成「元」，保留两位小数。
- 所有用户维度、订单维度的统计都必须排除测试账号：`users.is_test = 0`。
- 时间按 `orders.order_time`（下单时间）归属，不按支付时间。
- 「今天」视为 2025-12-31；「上个月」指 2025 年 11 月，「本月」指 2025 年 12 月，「今年」指 2025 年全年。

## 有效订单

有效订单指已支付且未全额退款的订单：`orders.status IN ('paid', 'completed')`。
- cancelled：下单后未支付即取消，不计入任何销售类指标。
- refunded：已全额退款，计入 GMV，但不计入净销售额和有效订单数。

## GMV（成交总额）

GMV = 已支付订单的实付金额之和，包含之后发生退款的订单。
SQL 口径：`SUM(pay_amount)`，条件 `status IN ('paid', 'completed', 'refunded')`，并排除测试账号。
注意：GMV 不包含 cancelled 订单；也不是 total_amount（未扣优惠的原价）之和。

## 净销售额（营收 / 销售额）

净销售额 = 有效订单的实付金额之和，即 GMV 扣除已退款订单。
SQL 口径：`SUM(pay_amount)`，条件 `status IN ('paid', 'completed')`，并排除测试账号。
用户说「销售额」「营收」「收入」时，默认指净销售额。

## 品类销售额

品类销售额按订单明细的成交金额统计：`SUM(order_items.item_amount)`，关联 products、categories，只统计有效订单（status IN ('paid', 'completed')）并排除测试账号。
注意：item_amount 未分摊订单级优惠，因此各品类销售额之和会略大于净销售额，这是预期内的差异。

## 退款率

退款率 = 退款订单数 / 已支付订单数。
- 分子：`status = 'refunded'` 的订单数（等价于 refunds 表中的订单数）。
- 分母：`status IN ('paid', 'completed', 'refunded')` 的订单数。
- 均排除测试账号，按下单时间归属月份。
注意：refund_requests 是「退款申请」表，其中 status = 'rejected' 的申请被驳回、没有产生真实退款，不能用 refund_requests 计算退款率。

## 客单价（AOV）

客单价 = 净销售额 / 有效订单数，单位换算成元。
SQL 口径：`SUM(pay_amount) / COUNT(*) / 100.0`，条件 `status IN ('paid', 'completed')`，排除测试账号。

## 下单用户数与复购率

- 下单用户数：统计周期内有至少一笔有效订单的去重用户数。
- 复购率：统计周期内有效订单数 ≥ 2 的用户数 / 下单用户数。
均排除测试账号。

## 活跃用户（MAU / DAU）

活跃用户以行为日志为准，不以下单为准：
- 月活跃用户（MAU）：当月在 user_events 中有任意事件（login / view / add_cart）的去重用户数。
- 日活跃用户（DAU）：当天在 user_events 中有任意事件的去重用户数。
user_events 中不含测试账号，但关联 users 表时仍建议加 `is_test = 0`。

## 新用户

新用户指注册日期（users.register_date）落在统计周期内的用户，排除测试账号。
「新用户首单」指新用户在注册当月产生的第一笔有效订单。

## 毛利与毛利率

- 毛利 = SUM(order_items.item_amount) - SUM(order_items.quantity * products.cost_price)，只统计有效订单，排除测试账号。
- 毛利率 = 毛利 / SUM(order_items.item_amount)。
注意：毛利计算基于明细成交价，未扣减订单级优惠。

## 区域

users.region_code 存英文编码，regions 表提供中文名映射：
- EAST = 华东，NORTH = 华北，SOUTH = 华南，CENTRAL = 华中，WEST = 西部。
用户按中文提问时，先用 regions 表转换为编码再过滤；结果展示时用中文名。区域归属以用户的注册区域为准。

## 渠道

- orders.channel 是下单端：app、web、mini_program（小程序）。
- users.acquire_channel 是拉新渠道：organic（自然流量）、ads（广告投放）、referral（老带新）。
两者含义不同。「哪个渠道的订单多」指 orders.channel；「哪个渠道拉来的用户」指 users.acquire_channel。

## 会员等级

users.member_level 取值：normal（普通）、silver（白银）、gold（黄金）。
