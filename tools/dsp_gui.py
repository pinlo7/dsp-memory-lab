"""dsp_gui.py — 戴森球计划 背包修改器 GUI（tkinter，标准库，无额外依赖）

    python tools/dsp_gui.py

操作:
  连接        自动定位（首次 ~分钟级全内存扫描，之后读缓存毫秒级）
  双击数量格  直接改数字，回车写回进程（自动钳到堆叠上限）
  刷新        重新读内存（游戏里拿放物品后点一下）
只改玩家本体背包 40 格的 count 字段；改前请先游戏内手动存档。
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from dsp_core import DspBag, DspError

def _app_dir() -> str:
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_FILE = os.path.join(_app_dir(), ".dsp_gui_settings.json")


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_settings(d: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.bag = None
        self.busy = False
        self.silent_fill_all = bool(_load_settings().get("silent_fill_all"))
        root.title("DSP 背包工具（教学项目）")
        root.geometry("560x640")

        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=8, pady=6)
        self.btn_conn = ttk.Button(bar, text="连接", command=self.on_connect)
        self.btn_conn.pack(side="left")
        self.btn_refresh = ttk.Button(bar, text="刷新", command=self.on_refresh, state="disabled")
        self.btn_refresh.pack(side="left", padx=6)
        self.btn_fill = ttk.Button(bar, text="堆满选中", command=self.on_fill_max, state="disabled")
        self.btn_fill.pack(side="left")
        self.btn_fillall = ttk.Button(bar, text="一键全堆满", command=self.on_fill_all, state="disabled")
        self.btn_fillall.pack(side="left", padx=6)  # 连接后可用
        self.btn_add = ttk.Button(bar, text="添加物品", command=self.on_add_item, state="disabled")
        self.btn_add.pack(side="left")
        self.btn_cont = ttk.Button(bar, text="其他容器", command=self.on_containers, state="disabled")
        self.btn_cont.pack(side="left", padx=6)
        self.var_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="自动刷新(2s)", variable=self.var_auto).pack(side="left", padx=6)
        self.lbl_status = ttk.Label(bar, text="未连接")
        self.lbl_status.pack(side="right")

        bar2 = ttk.Frame(root)
        bar2.pack(fill="x", padx=8)
        ttk.Label(bar2, text="沙土:").pack(side="left")
        self.var_sand = tk.StringVar(value="-")
        self.ent_sand = ttk.Entry(bar2, textvariable=self.var_sand, width=10, state="disabled")
        self.ent_sand.pack(side="left", padx=4)
        self.btn_sand = ttk.Button(bar2, text="设置", command=self.on_set_sand, state="disabled")
        self.btn_sand.pack(side="left")
        ttk.Label(bar2, text="(机甲采沙计数)", foreground="#888").pack(side="left", padx=6)

        self.var_autofill = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="自动堆满 每", variable=self.var_autofill).pack(side="left", padx=(14, 2))
        self.var_af_interval = tk.StringVar(value="1.0")
        ttk.Entry(bar2, textvariable=self.var_af_interval, width=5).pack(side="left")
        ttk.Label(bar2, text="秒").pack(side="left")

        cols = ("slot", "itemId", "name", "count", "stack")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=24)
        for c, t, w, anchor in (
            ("slot", "格", 44, "center"), ("itemId", "id", 56, "center"),
            ("name", "物品", 240, "w"), ("count", "数量", 80, "e"), ("stack", "上限", 66, "e"),
        ):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=anchor)
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", self.on_edit)
        self.tree.bind("<Return>", self.on_edit)
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="改数量…", command=self.on_edit)
        self.menu.add_command(label="堆满此格", command=self.on_fill_max)
        self.menu.add_command(label="清空此格", command=self.on_clear_slot)
        self.menu.add_command(label="添加到空格(搜索添加)", command=self.on_add_item)
        self.tree.bind("<Button-3>", self.on_rightclick)

        tip = ttk.Label(root, text="双击「数量」格修改；右键行=更多操作；「其他容器」可看/改机甲仓", foreground="#666")
        tip.pack(anchor="w", padx=10)

        self.msg_q = queue.Queue()
        self.root.after(100, self._poll_msg)
        self._auto_tick()

        self._af_stop = threading.Event()
        self._af_thread = None
        self._af_interval = 1.0
        self._af_changed = 0
        self._af_err = None
        self.var_autofill.trace_add("write", lambda *_: self._toggle_autofill())
        self.root.after(500, self._af_poll)

        # 后台预热: 游戏在跑就立刻开始扫描, 用户点"连接"时大概率已就绪
        self._prewarm_bag = None
        self._prewarm_thread = None
        try:
            from dsp_core import find_pid
            find_pid()
            self._prewarm_thread = threading.Thread(target=self._prewarm, daemon=True)
            self._prewarm_thread.start()
            self.lbl_status.config(text="检测到游戏，后台自动连接中…")
        except Exception:
            pass

    def _prewarm(self):
        try:
            b = DspBag(verbose=False)
            b.connect()
            self._prewarm_bag = b
        except Exception:
            self._prewarm_bag = None

    # ---------------------------------------------------------------- 后台线程
    def _run_bg(self, fn, done):
        if self.busy:
            return
        self.busy = True
        self.btn_conn.config(state="disabled")
        self.lbl_status.config(text="扫描中…（首次连接需数分钟）")

        def work():
            try:
                r = fn()
                self.msg_q.put(("ok", r, done))
            except Exception as e:  # noqa: BLE001
                self.msg_q.put(("err", str(e), done))
        threading.Thread(target=work, daemon=True).start()

    def _poll_msg(self):
        try:
            kind, payload, done = self.msg_q.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_msg)
            return
        self.busy = False
        self.btn_conn.config(state="normal")
        if kind == "err":
            self.lbl_status.config(text="出错")
            messagebox.showerror("错误", payload)
        else:
            done(payload)
        self.root.after(100, self._poll_msg)

    # ---------------------------------------------------------------- 交互
    def on_rightclick(self, ev):
        row = self.tree.identify_row(ev.y)
        if row:
            self.tree.selection_set(row)
            self.menu.post(ev.x_root, ev.y_root)

    def on_clear_slot(self):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])["values"]
        slot, has = int(item[0]), bool(item[1])
        if not has:
            return
        if not messagebox.askyesno("确认清空", f"清空第 {slot} 格（{item[2]} x{item[3]}）？"):
            return
        try:
            self.bag.clear_slot(slot)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("失败", str(e))
            return
        self.refresh_table()
        self.lbl_status.config(text=f"已清空第 {slot} 格")

    _CONT_LABEL = {  # 机甲五仓用途按容量+实测内容推断，标注为推测可后续校正
        ("Mecha", 4): "机甲仓·植物/采集物(推测)",
        ("Mecha", 1): None,  # 按内容区分：有料=空间翘曲器仓；组件仓=燃料棒等(推测)
        ("Mecha", 3): "机甲组件仓(推测)",
        ("Mecha", 5): "机甲手持/快捷仓(推测)",
    }

    def on_containers(self):
        """机甲仓/配送包等非背包容器 —— 树形展示，双击格子改数量，右键堆满。"""
        if not self.bag:
            return
        win = getattr(self, "_cont_win", None)
        if win and win.winfo_exists():
            win.lift()
            self._cont_reload(win)
            return
        win = tk.Toplevel(self.root)
        self._cont_win = win
        win.title("其他容器")
        win.geometry("560x460")
        win.transient(self.root)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="刷新", command=lambda: self._cont_reload(win)).pack(side="left")
        self._cont_status = ttk.Label(bar, text="", foreground="#555")
        self._cont_status.pack(side="left", padx=10)

        cols = ("slot", "name", "count", "stack")
        tree = ttk.Treeview(win, columns=cols, show="tree headings")
        tree.heading("#0", text="容器")
        tree.column("#0", width=250, anchor="w")
        for c, t, w in (("slot", "格", 46), ("name", "物品", 160), ("count", "数量", 70), ("stack", "上限", 66)):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="e" if c != "name" else "w")
        tree.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        tree.tag_configure("special", foreground="#a66a00")
        tree.tag_configure("empty", foreground="#999999")

        self._cont_tree = tree
        tree.bind("<Double-1>", lambda _e: self._cont_edit(win))
        cm = tk.Menu(win, tearoff=0)
        cm.add_command(label="堆满此格", command=lambda: self._cont_fill(win))
        tree.bind("<Button-3>", lambda e: (tree.identify_row(e.y) and tree.selection_set(tree.identify_row(e.y)),
                                           cm.post(e.x_root, e.y_root)))
        self._cont_menu = cm
        self._cont_reload(win)

    def _cont_reload(self, win):
        try:
            conts = [c for c in self.bag.find_containers() if not c["is_main"]]
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("读取失败", str(e), parent=win)
            return
        tree = self._cont_tree
        tree.delete(*tree.get_children())
        n_items = 0
        for c in sorted(conts, key=lambda x: (-x["layout_ok"], -x["len"])):
            lbl = f"{'◆' if c['layout_ok'] else '◇'} {c['owner']}  容量{c['len']}  [{c['path']}]"
            if not c["layout_ok"]:
                lbl += "  特殊布局·只读"
            pid = tree.insert("", "end", iid=f"arr{c['grids']:x}", text=lbl, open=True,
                              tags=() if c["layout_ok"] else ("special",),
                              values=("", "", "", ""))
            if not c["items"]:
                tree.insert(pid, "end", text="    （空）", tags=("empty",), values=("", "", "", ""))
            for r in c["items"]:
                tree.insert(pid, "end", text=f"    [{r['slot']}]",
                            values=(r["slot"], r["name"], r["count"], r["stack"]))
                n_items += 1
        self._cont_status.config(text=f"{len(conts)} 个容器 · {n_items} 件物品")

    def _cont_row(self):
        sel = self._cont_tree.selection()
        if not sel:
            return None
        vals = self._cont_tree.item(sel[0])["values"]
        if not vals or not vals[1]:
            return None
        parent = self._cont_tree.parent(sel[0])
        try:
            arr = int(parent.replace("arr", ""), 16)
        except ValueError:
            return None
        return arr, int(vals[0]), str(vals[1])

    def _cont_edit(self, win):
        row = self._cont_row()
        if not row:
            return
        arr, slot, name = row
        new = simpledialog.askinteger("改数量", f"{name}（上限自动钳制）:",
                                      parent=win, minvalue=0)
        if new is None:
            return
        try:
            applied = self.bag.set_count_in(arr, slot, new)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("写入失败", str(e), parent=win)
            return
        self._cont_reload(win)
        self.refresh_table()
        self.lbl_status.config(text=f"容器格 {name}: -> {applied}")

    def _cont_fill(self, win):
        row = self._cont_row()
        if not row:
            return
        arr, slot, name = row
        try:
            applied = self.bag.set_count_in(arr, slot, 10 ** 9)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("写入失败", str(e), parent=win)
            return
        self._cont_reload(win)
        self.refresh_table()
        self.lbl_status.config(text=f"容器格 {name} 已堆满 -> {applied}")

    def on_connect(self):
        def job():
            # 复用后台预热: 还在扫就等它, 扫完直接拿结果
            if self._prewarm_thread and self._prewarm_thread.is_alive():
                self._prewarm_thread.join()
            if self._prewarm_bag is not None:
                b, self._prewarm_bag = self._prewarm_bag, None
                return b
            b = DspBag(verbose=False)
            b.connect()
            return b

        self._run_bg(job, self._after_connect)

    def _after_connect(self, bag):
        if self.bag:
            try:
                self.bag.close()
            except Exception:
                pass
        self.bag = bag
        self.btn_refresh.config(state="normal")
        self.btn_fill.config(state="normal")
        self.btn_fillall.config(state="normal")
        self.btn_add.config(state="normal")
        self.btn_cont.config(state="normal")
        self.ent_sand.config(state="normal")
        self.btn_sand.config(state="normal")
        self.lbl_status.config(text=f"已连接 · {len(bag.names)} 物品名")
        self.refresh_table()

    def on_refresh(self):
        def job():
            if not self.bag:
                raise RuntimeError("先点连接")
            # 地址可能因游戏重启/读档失效：只验证不重扫（重扫交给用户点“连接”）
            if not self.bag._verify_chain():
                raise RuntimeError("地址已失效（游戏重启/读档？），请重新点「连接」")
            return self.bag.slots()

        if not self.busy:
            self._run_bg(job, lambda rows: self.render(rows))

    def refresh_table(self):
        try:
            self.render(self.bag.slots())
            if self.bag:
                self.var_sand.set(str(self.bag.get_sand()))
        except DspError as e:
            self.lbl_status.config(text=str(e))

    def on_set_sand(self):
        try:
            n = int(self.var_sand.get())
            if n < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("沙土", "请输入非负整数")
            return
        try:
            applied = self.bag.set_sand(n)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("失败", str(e))
            return
        self.var_sand.set(str(applied))
        self.lbl_status.config(text=f"沙土 -> {applied}")

    # ---------------------------------------------------------------- 堆满
    def on_fill_max(self):
        """把当前选中格数量设为其堆叠上限。"""
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("未选中", "先点选一行再堆满")
            return
        item = self.tree.item(sel[0])["values"]
        slot, has_item, stack = int(item[0]), bool(item[1]), int(item[4] or 0)
        if not has_item or stack <= 0:
            messagebox.showinfo("空格", f"第 {slot} 格是空的，无法堆满")
            return
        try:
            applied = self.bag.set_count(slot, stack)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("写入失败", str(e))
            return
        self.refresh_table()
        self.lbl_status.config(text=f"第 {slot} 格已堆满 -> {applied}")

    def _confirm_fill_all(self) -> bool:
        """堆满前确认，带「不再提示」勾选（写入 .dsp_gui_settings.json）。"""
        if self.silent_fill_all:
            return True
        box = {"v": False}
        win = tk.Toplevel(self.root)
        win.title("确认")
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, text="把所有有物品的格子一次堆满？\n（改前建议先游戏内存档）").pack(padx=16, pady=(12, 8))
        var = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="不再提示", variable=var).pack(anchor="w", padx=14)
        row = ttk.Frame(win)
        row.pack(pady=10)

        def yes():
            box["v"] = True
            close()

        def close():
            if var.get():
                self.silent_fill_all = True
                s = _load_settings()
                s["silent_fill_all"] = True
                _save_settings(s)
            win.destroy()

        ttk.Button(row, text="堆满", command=yes, width=8).pack(side="left", padx=6)
        ttk.Button(row, text="取消", command=close, width=8).pack(side="left")
        win.geometry("+%d+%d" % (self.root.winfo_rootx() + 120, self.root.winfo_rooty() + 120))
        win.grab_set()
        self.root.wait_window(win)
        return box["v"]

    def on_fill_all(self):
        """把所有有物品的格子一次堆满。"""
        if not self._confirm_fill_all():
            return
        try:
            rows = self.bag.slots()
        except DspError as e:
            messagebox.showerror("读取失败", str(e))
            return
        n = 0
        for r in rows:
            if r["itemId"] and r["stack"] > 0 and r["count"] != r["stack"]:
                self.bag.set_count(r["slot"], r["stack"])
                n += 1
        self.refresh_table()
        self.lbl_status.config(text=f"已堆满 {n} 格")

    # ---------------------------------------------------------------- 添加历史
    def _get_history(self) -> list[dict]:
        return _load_settings().get("add_history", [])

    def _record_history(self, item_id: int, name: str):
        s = _load_settings()
        hist = [h for h in s.get("add_history", []) if h.get("id") != item_id]
        hist.insert(0, {"id": item_id, "name": name})
        s["add_history"] = hist[:16]
        _save_settings(s)

    def _remove_history(self, item_id: int):
        s = _load_settings()
        s["add_history"] = [h for h in s.get("add_history", []) if h.get("id") != item_id]
        _save_settings(s)

    def _rebuild_chips(self, frame: ttk.Frame):
        for w in frame.winfo_children():
            w.destroy()
        hist = self._get_history()
        if not hist:
            ttk.Label(frame, text="（暂无历史 — 先搜索添加一次）").grid(row=0, column=0, sticky="w", padx=6)
            return
        for k, h in enumerate(hist):
            btn = ttk.Button(frame, text=h["name"], width=9,
                             command=lambda iid=h["id"]: self._quick_add(iid, frame))
            btn.grid(row=k // 8, column=k % 8, padx=2, pady=2, sticky="ew")
            btn.bind("<Button-3>", lambda _e, iid=h["id"]: self._chip_delete(iid, frame))

    def _chip_delete(self, item_id: int, frame: ttk.Frame):
        """右键删单条历史（不留确认，操作可逆：再添加一次就回来了）"""
        self._remove_history(item_id)
        self._rebuild_chips(frame)
        self.lbl_status.config(text=f"已从历史移除 {self.bag.names.get(item_id, item_id)}")

    def _quick_add(self, item_id: int, chips_frame: ttk.Frame):
        """点历史 chip：满堆加进第一个空格，窗口保留可连续添加。"""
        slot = self.bag.first_empty()
        if slot is None:
            messagebox.showinfo("背包已满", "没有空格了，先清空一格再加")
            return
        try:
            applied = self.bag.give(slot, item_id)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("失败", str(e))
            return
        self._record_history(item_id, self.bag.names.get(item_id, str(item_id)))
        self._rebuild_chips(chips_frame)
        self.refresh_table()
        self.lbl_status.config(text=f"快速添加: 第{slot}格 {self.bag.names.get(item_id)} x{applied}")

    # ---------------------------------------------------------------- 添加物品
    def on_add_item(self):
        if not self.bag:
            return
        win = tk.Toplevel(self.root)
        win.title("添加物品")
        win.geometry("420x560")
        win.transient(self.root)

        chips_box = ttk.LabelFrame(win, text="最近添加（点一下=满堆进空格 · 右键=移出历史）")
        chips_box.pack(fill="x", padx=8, pady=(8, 4))
        chips_frame = ttk.Frame(chips_box)
        chips_frame.pack(fill="x", padx=4, pady=4)
        self._rebuild_chips(chips_frame)

        ttk.Label(win, text="搜索（名称片段/id 均可）:").pack(anchor="w", padx=8, pady=(6, 2))
        var_q = tk.StringVar()
        ent = ttk.Entry(win, textvariable=var_q)
        ent.pack(fill="x", padx=8)
        ent.focus_set()

        lb = tk.Listbox(win, height=16)
        lb.pack(fill="both", expand=True, padx=8, pady=6)

        row = ttk.Frame(win)
        row.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(row, text="目标格:").pack(side="left")
        empty = [r["slot"] for r in self.bag.slots() if not r["itemId"]]
        var_slot = tk.StringVar(value="自动(第一个空格)" if empty else "覆盖选中格")
        slot_opts = [f"第 {s} 格" for s in empty]
        ttk.Combobox(row, textvariable=var_slot, width=16,
                     values=["自动(第一个空格)", "覆盖选中格"] + slot_opts).pack(side="left", padx=6)
        ttk.Label(row, text="数量:").pack(side="left")
        var_cnt = tk.StringVar(value="满堆")
        ttk.Entry(row, textvariable=var_cnt, width=8).pack(side="left")

        results = []

        def do_search(*_a):
            nonlocal results
            q = var_q.get().strip()
            lb.delete(0, "end")
            results = self.bag.search(q) if q else []
            for i, n, _s in results:
                lb.insert("end", f"  {i:<6} {n}")
            if len(results) == 1:
                lb.selection_set(0)

        def do_add(_e=None):
            if not results:
                return
            idx = lb.curselection()
            if not idx:
                messagebox.showinfo("选一个", "先在列表里选中一个物品", parent=win)
                return
            item_id = results[idx[0]][0]
            try:
                slot = resolve_slot()
                c = var_cnt.get().strip()
                count = None if c in ("满堆", "", "max") else int(c)
                applied = self.bag.give(slot, item_id, count, force=(var_slot.get() != "自动(第一个空格)"))
            except ValueError:
                messagebox.showwarning("数量", "数量填整数或「满堆」", parent=win)
                return
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("失败", str(e), parent=win)
                return
            self._record_history(item_id, self.bag.names.get(item_id, str(item_id)))
            win.destroy()
            self.refresh_table()
            self.lbl_status.config(text=f"第 {slot} 格 <- {self.bag.names.get(item_id)} x{applied}")

        def resolve_slot():
            v = var_slot.get()
            if v == "自动(第一个空格)":
                s = self.bag.first_empty()
                if s is None:
                    raise RuntimeError("背包已无空格")
                return s
            if v == "覆盖选中格":
                sel = self.tree.selection()
                if not sel:
                    raise RuntimeError("主表里先选中一格")
                return int(self.tree.item(sel[0])["values"][0])
            return int(v.replace("第", "").replace("格", "").strip())

        var_q.trace_add("write", do_search)
        ttk.Button(row, text="添加 (Enter)", command=do_add).pack(side="right")
        win.bind("<Return>", do_add)
        lb.bind("<Double-1>", do_add)

    def render(self, rows):
        sel = self.tree.selection()
        selslot = None
        if sel:
            try:
                selslot = int(self.tree.item(sel[0])["values"][0])
            except Exception:
                pass
        self.tree.delete(*self.tree.get_children())
        total = 0
        for r in rows:
            total += r["count"] if r["itemId"] else 0
            self.tree.insert(
                "", "end", iid=str(r["slot"]),
                values=(r["slot"], r["itemId"] or "", r["name"], r["count"] if r["itemId"] else "", r["stack"] or ""),
            )
        self.lbl_status.config(text=f"已用 {sum(1 for r in rows if r['itemId'])}/{len(rows)} · 共 {total}")
        if selslot is not None and self.tree.exists(str(selslot)):
            self.tree.selection_set(str(selslot))

    # ---------------------------------------------------------------- 编辑
    def on_edit(self, _ev):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])["values"]
        slot, name, old = int(item[0]), item[2], int(item[3] or 0)
        if not item[1]:
            messagebox.showinfo("空格", f"第 {slot} 格是空的，只能改已有物品的数量")
            return
        win = tk.Toplevel(self.root)
        win.title(f"改数量 · 第 {slot} 格 {name}")
        win.geometry("260x110")
        win.transient(self.root)
        v = tk.StringVar(value=str(old))
        e = ttk.Entry(win, textvariable=v, justify="right")
        e.pack(pady=14, ipadx=8)

        def commit(_e=None):
            try:
                n = int(v.get())
                if n < 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("数量", "请输入非负整数", parent=win)
                return
            try:
                applied = self.bag.set_count(slot, n)
            except Exception as err:  # noqa: BLE001
                messagebox.showerror("写入失败", str(err), parent=win)
                return
            win.destroy()
            self.refresh_table()
            applied_note = f"（钳到上限）" if applied != n else ""
            self.lbl_status.config(text=f"第 {slot} 格: {old} -> {applied} {applied_note}")

        ttk.Button(win, text="写入 (Enter)", command=commit).pack()
        e.focus_set()
        e.select_range(0, "end")
        win.bind("<Return>", commit)

    # ---------------------------------------------------------------- 自动刷新
    def _auto_tick(self):
        if self.var_auto.get() and self.bag and not self.busy:
            self.refresh_table()
        self.root.after(2000, self._auto_tick)

    # ---------------------------------------------------------------- 自动堆满
    # 注意: tkinter 非线程安全 —— 工作线程绝不碰 tk 变量/控件，
    # 间隔由主线程读好后存 self._af_interval(float)，UI 更新走 root.after 回主线程。
    def _toggle_autofill(self):
        on = self.var_autofill.get()
        if on:
            if not self.bag:
                self.var_autofill.set(False)
                messagebox.showinfo("未连接", "先连接游戏再开自动堆满")
                return
            try:
                iv = float(self.var_af_interval.get())
                if iv < 0.2:
                    raise ValueError
            except ValueError:
                self.var_autofill.set(False)
                messagebox.showwarning("间隔", "间隔至少 0.2 秒（填数字，如 1.0）")
                return
            self._af_interval = iv
            self._af_stop.clear()
            self._af_thread = threading.Thread(target=self._autofill_loop, daemon=True)
            self._af_thread.start()
            self.lbl_status.config(text=f"自动堆满已开启（每 {iv}s）")
        else:
            self._af_stop.set()
            self.lbl_status.config(text="自动堆满已关闭")

    def _autofill_loop(self):
        """工作线程：只做内存读写 + 置普通属性，绝不碰 tkinter（连 root.after 都不行）。"""
        while not self._af_stop.is_set():
            changed = 0
            try:
                for r in self.bag.slots():
                    if self._af_stop.is_set():
                        return
                    if r["itemId"] and r["stack"] and r["count"] < r["stack"]:
                        self.bag.set_count(r["slot"], r["stack"])
                        changed += 1
            except Exception as e:  # noqa: BLE001
                self._af_err = str(e)
                return
            if changed:
                self._af_changed += changed
            self._af_stop.wait(self._af_interval)

    def _af_poll(self):
        """主线程轮询工作线程的结果（tkinter 更新只能在这里做）。"""
        try:
            if self._af_err:
                msg, self._af_err = self._af_err, None
                self.var_autofill.set(False)
                self.lbl_status.config(text=f"自动堆满出错已停: {msg[:40]}")
            elif self._af_changed:
                n, self._af_changed = self._af_changed, 0
                self.refresh_table()
                self.lbl_status.config(text=f"自动堆满: 累计补了 {n} 格")
        finally:
            self.root.after(500, self._af_poll)


def main():
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
