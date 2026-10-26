"""Local presentation preferences; independent of product identity and the ledger."""
from app.domain.finance import require
from app.persistence.store import Store


DEFAULT_GROUPS = []
DEFAULT_FUNDS = {}


def text_field(value, maximum, label, empty=False):
    require(isinstance(value, str), "INVALID_SIDEBAR", f"{label}须为文字")
    value = value.strip()
    require((empty or bool(value)) and len(value) <= maximum,
            "INVALID_SIDEBAR", f"{label}请填写{'0' if empty else '1'}–{maximum}个字符")
    return value


class FundSidebarMixin:
    def sidebar_catalog(self, c, funds):
        stored = Store.doc(c, "fund_sidebar", "catalog")
        if stored is not None:
            by_id = {f["fund_id"]: f for f in stored["funds"]}
            # New products stay unclassified after the user has customized the catalog.
            return dict(stored, funds=[by_id.get(f["id"], {
                "fund_id": f["id"], "group_id": None, "display_name": f["name"], "theme": ""
            }) for f in funds])
        rows = []
        for f in funds:
            group, name, theme = DEFAULT_FUNDS.get(f.get("fund_code"), (None, f["name"], ""))
            # Default shortened names describe C shares only; other classes keep their identity.
            if f.get("share_class") != "C":
                name = f["name"]
            rows.append({"fund_id": f["id"], "group_id": group, "display_name": name, "theme": theme})
        used = {f["group_id"] for f in rows}
        return {"groups": [dict(g) for g in DEFAULT_GROUPS if g["id"] in used], "funds": rows, "_version": 0}

    def save_fund_sidebar(self, p):
        require(set(p) <= {"groups", "funds", "expected_version"}, "INVALID_SIDEBAR", "分类设置包含未知字段")
        groups, rows = p.get("groups"), p.get("funds")
        require(isinstance(groups, list) and len(groups) <= 100 and isinstance(rows, list),
                "INVALID_SIDEBAR", "请提供分类和基金列表，分类最多100个")
        version = p.get("expected_version")
        require(type(version) is int and version >= 0, "INVALID_SIDEBAR", "请提供当前分类版本")
        clean_groups, clean_rows, ids, fund_ids = [], [], set(), set()
        for g in groups:
            require(isinstance(g, dict) and set(g) == {"id", "name"}, "INVALID_SIDEBAR", "分类字段不完整")
            id = text_field(g["id"], 80, "分类标识")
            require(id not in ids and id != "unclassified", "INVALID_SIDEBAR", "分类标识重复或为系统保留值")
            ids.add(id)
            clean_groups.append({"id": id, "name": text_field(g["name"], 40, "分类名称")})
        with self.store.transaction() as c:
            existing = {f["id"] for f in Store.docs(c, "fund")}
            for f in rows:
                require(isinstance(f, dict) and set(f) == {"fund_id", "group_id", "display_name", "theme"},
                        "INVALID_SIDEBAR", "基金显示设置字段不完整")
                fid, group = f["fund_id"], f["group_id"]
                require(isinstance(fid, str) and fid in existing and fid not in fund_ids,
                        "INVALID_SIDEBAR", "基金不存在或重复")
                require(group is None or isinstance(group, str) and group in ids,
                        "INVALID_SIDEBAR", "所属分类不存在")
                fund_ids.add(fid)
                clean_rows.append({"fund_id": fid, "group_id": group,
                                   "display_name": text_field(f["display_name"], 80, "基金显示名"),
                                   "theme": text_field(f["theme"], 40, "观察标签", empty=True)})
            Store.put(c, "fund_sidebar", "catalog", {"groups": clean_groups, "funds": clean_rows}, version)
            return self.sidebar_catalog(c, Store.docs(c, "fund"))
