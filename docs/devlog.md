## 2026-07-20锛圖ay 1锛�

**鍋氫簡**
- 鐜�澧冩惌寤猴細uv + Python 3.12 + DeepSeek API 璺戦��
- 鎵嬪啓 40 琛屾渶灏� Agent 寰�鐜�锛岀悊瑙� while 寰�鐜�鍑哄彛 = 娌℃湁 tool_calls
- v0 瀹屾垚锛氳嚜瀹氫箟 IR + DeepSeek codec锛�17 鏉℃祴璇曞叏缁�
- 瀹屾垚浜虹敓绗�涓�娆� code review锛�4 涓�鎺㈤拡锛�1 涓�鐪� bug锛堢┖ messages 鏈� fail-fast锛夛紝2 涓�鏀硅繘椤�

**鍗″湪鍝� / 鎬庝箞瑙ｅ喅**
- OPENSSL_Uplink 鎶ラ敊 鈫� 鏌ュ嚭鏄� SSLKEYLOGFILE 鐜�澧冨彉閲忓啿绐� 鈫� 娓呴櫎瑙ｅ喅
- 鎺㈤拡 3 鎶� AttributeError 鈫� 鏄�鎴戞帰閽堝啓閿欎笉鏄�浠ｇ爜閿� 鈫� 鏁欒��锛氭帰閽堟姤閿欏厛鎬�鐤戞帰閽�

**瀛﹀埌鐨勶紙鑷�宸辩殑璇濓級**
- LLM API 鏄�鏃犵姸鎬佺殑锛�"璁板繂"灏辨槸 messages 鏁扮粍鏈�韬�
- 瀹炴祴 DeepSeek 涓婁笅鏂囩紦瀛樺懡涓�锛歝ache_read=256 / input=281

**鏄庡ぉ绗�涓�浠朵簨**锛氳�� plan agent 璇绘枃妗� 5.1锛屽嚭 Agent Loop 瀹炵幇璁″垝

---

## 2026-07-21锛圖ay 2锛�

**鍋氫簡**
- 灏� day0 鐨勮８ while 寰�鐜�閲嶆瀯涓哄簱褰㈡�侊細`Agent(config, backend).run(task, workdir) 鈫� Trajectory`
- 鏂板缓 `AgentConfig`銆乣ModelBackend` 鍗忚��銆乣DeepSeekBackend`銆乣Trajectory`锛圫QLite 浜嬩欢娴佸瓨鍌�锛�
- 宸ュ叿绯荤粺鐙�绔嬩负 `vague_code/agent/tools.py`锛歚Tool` dataclass + `bind(workdir)` 宸ュ巶妯″紡
- CLI 楠ㄦ灦锛坅rgparse + Rich 娓叉煋锛�
- 琛ュ畬 ADR-0004锛堝伐鍏锋敞鍐岃〃锛夈�丄DR-0005锛堟祦寮忎簨浠� IR锛夈�丄DR-0006锛堥噸璇曚笌妫�鏌ョ偣锛�
- 琛ュ畬瀹炵幇璁″垝 0002锝�0004
- 76 鏉℃祴璇曞叏缁� + ruff/mypy

**瀛﹀埌鐨�**
- 涓�浠�"瀹炵幇璁″垝 鈫� 浠ｇ爜 鈫� 娴嬭瘯"鐨勬祦姘寸嚎姣旂洿鎺ュ啓浠ｇ爜蹇�涓ゅ�嶏紙鍥犱负鏈夎�″垝锛孉I agent 涓嶉渶瑕佺寽涓婁笅鏂囷級
- ADR 鏄�寮傛�ュ喅绛栫殑鍏抽敭锛氬啓璁″垝鏃剁湅鍒� ADR-0006 宸查�勫畾 `TransportConfig`銆乣retry/timeout` 鏃嬮挳浣嶇疆锛屽悗缁�璁捐�″氨鍙�浠ョ洿鎺ュ紩鐢ㄨ�屼笉鍥為��

---

## 2026-07-22锛圖ay 3锛�

**鍋氫簡**
- 瀹炵幇缁熶竴娴佸紡浜嬩欢妯″瀷锛�9 绉� `StreamEvent` dataclass + `StreamEventVisitor` 鍗忚��锛�
- DeepSeek codec 鏂板�� `DeepSeekStreamDecoder`锛�5 姝ョ姸鎬佹満锛歵hinking 杈圭晫鎺ㄦ柇銆乼ool_call 鎸� index 杩借釜銆乫inish/usage 寤惰繜鍙戝皠锛�
- `ModelBackend` 鎵╁睍 `stream()` 鏂规硶锛宍_stream_from` 閫傞厤鍣ㄥ吋瀹归潪娴佸紡鍚庣��
- `RunHandle` 杩�浠ｅ櫒妯″紡锛欳LI 瀹炴椂鎷夊彇浜嬩欢锛宍Agent.run()` 鍚戝悗鍏煎��
- `TransportConfig` 寮曞叆锛屼紶杈撳眰璇�涔変笌涓氬姟閰嶇疆鍒嗙��
- CLI 鍔犲叆 `RichStreamVisitor` + `--stream`/`--no-stream`
- 7 濂� golden fixture 蹇�鐓� + 棰濆�� 10+ 娴佸紡杈圭晫鍗曟祴

**瀛﹀埌鐨�**
- 娴佸紡浠ｇ爜閲� bug 鏈�澶氱殑鐐规案杩滄槸 JSON 澧為噺鎷兼帴鈥斺�旈泦涓�鍦ㄤ竴涓� `_StreamAggregator` 閲岋紝鍙�鍐欎竴娆★紝鎵�鏈� codec 澶嶇敤
- `hasattr(backend, "stream")` 閫傞厤鍣ㄨ�� FakeBackend锛堜粎 `complete`锛夐浂鏀瑰姩灏辫兘杩涙祦寮忕�＄嚎鈥斺�旇瘎娴嬫椂娴佸紡/闈炴祦寮忓垏鎹㈤浂鎴愭湰
- 宸ュ叿鍙傛暟 `json.loads` 鏀惧湪 `MessageEnd` 涔嬪悗鎵归噺鎵ц�岋紝涓嶅湪姣忎釜 `ToolUseEnd` 鏃堕�愪釜瑙ｆ瀽鈥斺�旇繖淇濊瘉浜嗗啿绐佸彲涓茶�屽寲鐨勫熀鍑嗗墠鎻愶紙妯″瀷杈撳嚭椤哄簭 = 涓茶�屽簭锛�

---

## 2026-07-23锛圖ay 4锛�

**鍋氫簡**
- 瀹炵幇 ADR-0006 鍏ㄩ儴 10 鑺傦細涓ゅ眰閲嶈瘯锛圫DK `max_retries=2` + Loop 鎸囨暟閫�閬垮叏鎶栧姩锛夈�佸紓甯哥粏鍒嗙被锛�10 绉� + 1 鍏滃簳锛夈�佸紓甯搁┍鍔� retry 鍐崇瓥
- `RetryPolicy` 绾�鍑芥暟 + `classify_llm_error` 鍙�鑴辩�� Agent 鍗曠嫭鍗曟祴
- `RetryNotice` 浣滀负绗� 10 绉� StreamEvent锛欳LI 瀹炴椂鎵撳嵃 `鈿� 璇锋眰澶辫触锛孨 绉掑悗閲嶈瘯锛堢�� n 娆★級`
- 妫�鏌ョ偣鏈哄埗锛氭瘡杞� LLM 鍝嶅簲鍚庛�佸伐鍏锋墽琛屽墠 `traj.persist()`锛屽穿婧冩仮澶嶈蛋浜嬪姟璇�涔夛紙"鍏ㄥ洖婊�"锛�
- `Trajectory.from_db` + `Agent.resume()`锛氫粠 SQLite 鎭㈠�嶈建杩癸紝璇嗗埆鏈�瀹屾垚宸ュ叿骞堕噸鍋�
- 瀹屾垚涓よ疆 review锛堟垜鎶� bug 鈫� AI 澶嶆牳 鈫� 鍙戠幇 P0 off-by-one 鈫� 淇�澶� + 鍥炲綊娴嬭瘯锛�
- CLI 娴嬭瘯濂椾欢锛�29 涓� mock 绠＄嚎娴嬭瘯 + 4 涓�瀛愯繘绋嬫祴璇� + `--export-jsonl` 鐩�褰曟��娴�
- 鍏冩暟鎹�琛岋紙`Run X finished`锛変粠榛樿�よ緭鍑虹Щ鍒� `--verbose`
- 205 鏉℃祴璇曞叏缁匡紝ruff/mypy 閫�

**鍗″湪鍝� / 鎬庝箞瑙ｅ喅**
- 绗�涓�鐗� resume 鐨� turn 璁＄畻鐢� `_count_turns`锛坄max(turn) + 1`锛夋帹瀵硷紝瀵艰嚧宸ュ叿浜嬩欢鎸傞敊 turn 鈫� 鏀圭敤 `last_llm.turn` 鍋氭潈濞� turn
- 鍥炲綊娴嬭瘯鐢� `max_turns=5` 鎺╃洊浜� off-by-one锛堝穿婧冧簬 T=0 鈫� resume 璺宠繃浜� T=1 鐨� LLM 璋冪敤锛�5 杞�鐪嬩笉鍑烘潵锛夆啋 鏁欒��锛氳竟鐣屽洖褰掑繀椤荤敤**鏈�灏忚兘鏆撮湶 bug 鐨勬暟鍊�**锛坄max_turns=2`锛�

**瀛﹀埌鐨�**
- Review 浜у嚭涓嶆槸浣犳姤瀵逛簡鍑犱釜 bug锛岃�屾槸浣�**鎶ヤ簡鍊欓�夐棶棰�**鈥斺�旇��闄嶇骇涓嶄涪浜猴紝琚�楠岃瘉鎵嶆槸鐪� bug
- 鎸佷箙鍖栧彧鏈変袱澶勶紙checkpoint + finally锛夛紝鎵�浠ユ墍鏈夌粓鎬佸洖澶嶉兘鍜� `run_end` 鍚屾壒鍘熷瓙钀界洏鈥斺�旇繖鏄�宕╂簝鎭㈠�嶆�ｇ‘鎬х殑鏍瑰熀锛岄潰璇曡兘璁�"涓轰粈涔堝彧鏈変袱涓� persist 鐐硅�屼笉鏄�鍒板�勫啓"
- CLI 娴嬭瘯鍒嗗洓灞傦紙鍙傛暟 鈫� 閰嶇疆浼犻�� 鈫� mock 鍏ㄧ�￠亾 鈫� 瀛愯繘绋嬶級鈥斺�旀瘡涓�灞傞兘鍦ㄤ笂涓�灞傚け璐ヤ笉浜嗙殑绐楀彛閲屾壘婕忔礊

---

## 宸茬煡璁板綍锛堟湭琛ワ級

寰呰ˉ锛欴ay 2 鐨�"鍗″湪鍝�"娌℃湁璁帮紝鍥犱负褰撳ぉ閮芥槸椤轰骇娌℃湁闃诲�炪�侱ay 3 涔熸病鏈夐樆濉為」鈥斺�旀祦寮� codec 鐨勫崟娴嬩竴娆￠�氳繃鍚庢病鏈夊洖閫�锛屽彧鏈夋�ｅ父鐨勮凯浠ｃ�侱ay 4 鐨勯樆濉為」鍏ㄥ湪"鎬庝箞瑙ｅ喅"閲岃�颁簡銆�
---

## 2026-08-04（TUI v2 整体重写）

**做了**
- 学习参考包：`tui-reference-pack/`（firstcoder 的 Textual TUI，3900 行 / 30 文件 / 267 测试），产出架构学习笔记与对比报告
- 按 `docs/plans/0017-tui-rewrite.md` 分 6 个里程碑重写 `vague_code/tui/`，每步独立提交：
  - M1 骨架+spike（e585fa4）：新分层 + 参考包主题 + VagueCodeMarkdown 在 textual 8.2.8 可移植验证
  - M2 事件流（48bc6f1）：`VagueCodeAgentRunner` 线程桥 + 流式 Markdown 三层缓冲 + agent 小改①（on_tool_result 带 tool id）
  - M3 工具活动流（3b3ab67）：thinking/streaming/running 动画 + 回合 metrics + 工具状态机
  - M4 命令系统（905d0d3）：CompositeCommandHandler + picker + 输入历史 + Esc 二次中断 + guidance 队列（agent 小改③）
  - M5 权限审查（5ed8656）：prewrite diff + 拒绝理由反馈闭环（agent 小改②）
  - M6 收尾（1131b20）：resume 轨迹重放 + 清理
- 新增 ADR-0019（TUI v2 分层重写），全量文档同步

**学到的**
- 参考包的流式渲染三层缓冲（事件线程锁+代际号 → UI 线程 buffer → 0.2s timer flush + update future guard）是解决"流式 Markdown 不闪烁、不乱序、不半更新"的完整方案，比每 delta 全量拼接的 v1 强一个量级
- 事件通道不对称是 UI 正确性的隐患：tool call 走流、result 走回调且无 id → 无法关联状态；统一到"回调直达单一事实源（transcript）+ token 过期过滤"后，重放/中断/恢复都免费获得
- Textual 的坑：`run_worker(thread=True)` 传**函数调用结果**会在线程里执行调用本身（UI 线程阻塞）；`is_mounted` 是接收 widget 的方法而非布尔属性；mixin 桩方法会遮蔽 App 真实方法（MRO），需放 TYPE_CHECKING 块

**明天第一件事**：附件粘贴语义（Agent 目前只接受任务文本）或继续 eval 真数字基线

---

## 2026-08-07锛�10 棰樺熀绾� + 娑堣瀺瀹為獙锛�78 runs锛孿.08锛�

**鍋氫簡**
- 閲嶈窇 08-06 瀹氭�堣��涓�鏂�鐨� 10 棰樺叏閲忓熀绾匡細鏍稿績灞� 10 瀹炰緥 x k3 + 娑堣瀺灞� 8 瀹炰緥 x 3 鍏抽棴閰嶇疆 x k2 = 78 runs锛�3 杩涚▼骞惰�岋紙WMI锛夛紝11:12 鍚�鍔� 16:24 鍏ㄩ儴瀹屾垚锛岄浂寮傚父
- 鏍稿績灞� pass^3 = 8/10 婊″垎锛�80%锛夛紝杈炬爣 ADR-0020锛�>=2/3锛夛紱21612 鏈�寮� 1/3銆�13031 2/3锛屽け璐ユā寮忓潎涓� no_diff 闆剁紪杈�
- 娑堣瀺锛氬叧 RepoMap 闆舵崯澶� 16/16锛涘叧鍘嬬缉 15/16銆佸叧骞跺彂 14/16锛屾崯澶卞叏闆嗕腑鍦� 21612
- 鐩戠潱澶嶉獙锛歴tagnant 1.3%銆佺洃鐫ｅ�為噺 6.8%锛圽.71/\.08锛夛紝ADR-0020 鏍囧噯 4/5 鍙屽弻杈炬爣
- 鍘嬬缉楠岃瘉瀹氭�堬細87 runs 绱�璁★紙9+78锛夊叏閮ㄤ粎 stale_snip锛屽悗鍗婃�靛洓灞傞浂瑙﹀彂
- 浜у嚭鎶ュ憡锛歳uns/eval/b10_baseline_report.md + 浜ゆ帴 docs/handoff/2026-08-07-vague-code-baseline-complete.md

**鍗″湪鍝� / 鎬庝箞瑙ｅ喅**
- 瀹炰緥鎵ц�岄『搴忎笌鐩磋�変笉绗︼細cli 鎸� tasks.json 鍘熷�嬮『搴忚繃婊よ�岄潪 --instances 鍙傛暟椤哄簭锛�13480 鍏堜簬 20590锛夆�斺�旀煡 harness 婧愮爜纭�璁わ紝涓嶅奖鍝嶇粨鏋�
- results JSON 鍙�鍦ㄨ繘绋嬬粨鏉熸墠钀界洏锛屼腑閫旂湅涓嶅埌鍒ゅ畾鈥斺�旂湅 db run_end + stats 棰勫垽锛屾潈濞佹暟瀛楃瓑杩涚▼缁撴潫

**瀛﹀埌鐨�**
- 娑堣瀺瀹為獙鐨勫尯鍒嗗害鍙椾换鍔￠泦闅惧害鍒嗗竷闄愬埗锛�8 棰樺叏杩囨椂涓夊彉閲忓紑鍏虫棤宸�寮傦紝鎹熷け鐨勫尯鍒嗗害鍏ㄥ帇鍦� 21612 涓�棰樹笂鈥斺�旂粨璁鸿�佸甫"浠诲姟闆嗗亸鏄�"鐨勯檺瀹�
- 澶辫触妯″紡闅忛厤缃�杩佺Щ锛�21612锛氬熀绾� no_diff -> 鍏冲苟鍙� f2p:fail锛夎�存槑閰嶇疆褰卞搷鐨勬槸淇�澶嶈�屼负璺�寰勮�岄潪绠�鍗曞ソ鍧�

**鏄庡ぉ绗�涓�浠朵簨**锛�21612 澶辫触鍒嗙被锛圥2 鍏�绫伙級锛屾垨鍘嬬缉鍚庡崐娈甸獙璇侊紙璋冧綆 auto_compact_threshold 0.85->0.5 鍗曢�樿窇锛�
