"""HugeGraph 1.7 PD 模式原生 REST 客户端。

只使用 Python 标准库，避免在 stg-121 上额外安装 requests。
PD 模式所有图内接口统一走：
  {base}/graphspaces/{graphspace}/graphs/{graph}/...
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import threading
import zlib
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .semantic import ProfileSemanticLayer


class HugeGraphError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HugeGraph {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


class HugeGraphClient:
    def __init__(self, base_url: Optional[str] = None,
                 graphspace: Optional[str] = None,
                 graph: Optional[str] = None,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 timeout: float = 20.0):
        self.base_url = (base_url or os.getenv(
            "PROFILE_HG_URL", "http://10.129.33.121:8080")).rstrip("/")
        self.graphspace = graphspace or os.getenv("PROFILE_HG_GRAPHSPACE", "profile_ai")
        self.graph = graph or os.getenv("PROFILE_HG_GRAPH", "profile")
        self.username = username or os.getenv("PROFILE_HG_USER")
        self.password = password or os.getenv("PROFILE_HG_PASSWORD")
        self.timeout = timeout
        self._auth_header = None
        if self.username:
            token = base64.b64encode(
                f"{self.username}:{self.password or ''}".encode()).decode()
            self._auth_header = f"Basic {token}"
        self._lock = threading.Lock()
        self.semantic = ProfileSemanticLayer()

    # ------------------------------------------------------------------ HTTP
    @staticmethod
    def _decode(raw: bytes, encoding: Optional[str]) -> str:
        """HugeGraph 默认 gzip 压缩响应，需在解析前解压。"""
        encoding = (encoding or "").lower()
        if "gzip" in encoding:
            raw = gzip.decompress(raw)
        elif "deflate" in encoding:
            raw = zlib.decompress(raw)
        return raw.decode("utf-8", "replace")

    def _url(self, path: str) -> str:
        return (f"{self.base_url}/graphspaces/{quote(self.graphspace, safe='')}"
                f"/graphs/{quote(self.graph, safe='')}/{path.lstrip('/')}")

    def _space_url(self, path: str) -> str:
        return (f"{self.base_url}/graphspaces/{quote(self.graphspace, safe='')}"
                f"/{path.lstrip('/')}")

    def request(self, method: str, url: str, body: Any = None,
                params: Optional[dict] = None,
                ok_statuses: Iterable[int] = (200, 201, 204)) -> Any:
        if params:
            query = urlencode({k: v for k, v in params.items() if v is not None},
                              doseq=True, quote_via=quote)
            if query:
                url = f"{url}?{query}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = self._decode(resp.read(), resp.headers.get("Content-Encoding"))
                if resp.status not in ok_statuses:
                    raise HugeGraphError(resp.status, raw, raw)
                return json.loads(raw) if raw.strip() else {}
        except HTTPError as e:
            raw = self._decode(e.read(), e.headers.get("Content-Encoding"))
            try:
                parsed = json.loads(raw)
                msg = parsed.get("message") or parsed.get("exception") or raw
            except Exception:
                msg = raw
            raise HugeGraphError(e.code, str(msg), raw) from e
        except URLError as e:
            raise HugeGraphError(0, f"无法连接 {url}: {e.reason}") from e

    # ------------------------------------------------------------ graphspace
    def list_graphspaces(self) -> List[dict]:
        data = self.request("GET", f"{self.base_url}/graphspaces")
        rows = data.get("graphSpaces") or data.get("graphspaces") or []
        # 1.7 返回名称字符串数组，兼容旧版对象数组。
        return [r if isinstance(r, dict) else {"name": r} for r in rows]

    def ensure_graphspace(self, payload: Optional[dict] = None) -> dict:
        names = {g.get("name") for g in self.list_graphspaces()}
        if self.graphspace in names:
            return {"created": False, "name": self.graphspace}
        body = {
            "name": self.graphspace,
            "nickname": "画像AI语义层",
            "description": "画像资产本体、时态关系与 Agent 可信上下文",
            "cpu_limit": 4,
            "memory_limit": 4096,
            "storage_limit": 10240,
            "max_graph_number": 10,
            "max_role_number": 20,
            "auth": False,
            "oltp_namespace": "",
            "olap_namespace": "",
            "storage_namespace": "",
            "configs": {},
        }
        if payload:
            body.update(payload)
        self.request("POST", f"{self.base_url}/graphspaces", body, ok_statuses=(201,))
        return {"created": True, "name": self.graphspace}

    def list_graphs(self) -> List[str]:
        data = self.request("GET", self._space_url("graphs"))
        return data.get("graphs", [])

    def ensure_graph(self) -> dict:
        if self.graph in self.list_graphs():
            return {"created": False, "name": self.graph}
        body = {"backend": "hstore", "serializer": "binary", "store": self.graph}
        self.request("POST", self._space_url(f"graphs/{self.graph}"), body,
                     ok_statuses=(201,))
        return {"created": True, "name": self.graph}

    def graph_info(self) -> dict:
        return self.request("GET", self._space_url(f"graphs/{self.graph}"))

    def health(self) -> dict:
        try:
            info = self.graph_info()
            schema = self.get_schema()
            return {
                "ok": True,
                "base_url": self.base_url,
                "graphspace": self.graphspace,
                "graph": self.graph,
                "graph_info": info,
                "schema": {
                    "propertykeys": len(schema.get("propertykeys", [])),
                    "vertexlabels": len(schema.get("vertexlabels", [])),
                    "edgelabels": len(schema.get("edgelabels", [])),
                    "indexlabels": len(schema.get("indexlabels", [])),
                },
            }
        except HugeGraphError as e:
            return {"ok": False, "error": str(e), "base_url": self.base_url,
                    "graphspace": self.graphspace, "graph": self.graph}

    # ---------------------------------------------------------------- schema
    def get_schema(self) -> dict:
        return self.request("GET", self._url("schema"))

    def create_property_key(self, name: str, spec: dict) -> dict:
        body = {
            "name": name,
            "data_type": spec.get("data_type", "TEXT"),
            "cardinality": spec.get("cardinality", "SINGLE"),
        }
        return self.request("POST", self._url("schema/propertykeys"), body,
                            ok_statuses=(201, 202))

    def create_vertex_label(self, cls: dict) -> dict:
        props = cls.get("properties", [])
        body = {
            "name": cls["id"],
            "id_strategy": "CUSTOMIZE_STRING",
            "properties": props,
            "nullable_keys": props,
            "enable_label_index": True,
        }
        return self.request("POST", self._url("schema/vertexlabels"), body,
                            ok_statuses=(201, 202))

    def create_edge_label(self, edge: dict) -> dict:
        sort_keys = edge.get("sort_keys", [])
        props = edge.get("properties", [])
        body = {
            "name": edge["name"],
            "source_label": edge["source_label"],
            "target_label": edge["target_label"],
            "properties": props,
            "nullable_keys": [p for p in props if p not in sort_keys],
            "sort_keys": sort_keys,
            "enable_label_index": True,
        }
        # HugeGraph 要求只有 MULTIPLE 频次的边才能声明 sort_keys。
        if sort_keys:
            body["frequency"] = "MULTIPLE"
        return self.request("POST", self._url("schema/edgelabels"), body,
                            ok_statuses=(201, 202))

    def create_index_label(self, name: str, base_value: str,
                           field: str, index_type: str = "SECONDARY") -> dict:
        body = {
            "name": name,
            "base_type": "VERTEX_LABEL",
            "base_value": base_value,
            "index_type": index_type,
            "fields": [field],
        }
        return self.request("POST", self._url("schema/indexlabels"), body,
                            ok_statuses=(201, 202))

    def append_vertex_label_properties(self, name: str,
                                       properties: List[str]) -> dict:
        body = {"name": name, "properties": properties,
                "nullable_keys": properties}
        return self.request(
            "PUT", self._url(f"schema/vertexlabels/{quote(name, safe='')}"),
            body, params={"action": "append"})

    def append_edge_label_properties(self, name: str,
                                     properties: List[str],
                                     sort_keys: Optional[List[str]] = None) -> dict:
        sort_keys = sort_keys or []
        body = {"name": name, "properties": properties,
                "nullable_keys": [p for p in properties if p not in sort_keys]}
        return self.request(
            "PUT", self._url(f"schema/edgelabels/{quote(name, safe='')}"),
            body, params={"action": "append"})

    def ensure_schema(self) -> dict:
        schema = self.get_schema()
        existing_pk = {p.get("name") for p in schema.get("propertykeys", [])}
        existing_vl = {v.get("name"): v for v in schema.get("vertexlabels", [])}
        existing_el = {e.get("name"): e for e in schema.get("edgelabels", [])}
        existing_il = {i.get("name") for i in schema.get("indexlabels", [])}

        created = {"propertykeys": [], "vertexlabels": [], "edgelabels": [],
                   "indexlabels": []}
        existed = {"propertykeys": [], "vertexlabels": [], "edgelabels": [],
                   "indexlabels": []}
        updated = {"vertexlabels": [], "edgelabels": []}
        errors: List[dict] = []

        for name, spec in self.semantic.properties.items():
            if name in existing_pk:
                existed["propertykeys"].append(name)
                continue
            try:
                self.create_property_key(name, spec)
                existing_pk.add(name)
                created["propertykeys"].append(name)
            except HugeGraphError as e:
                errors.append({"kind": "propertykey", "name": name, "error": str(e)})

        for cls in self.semantic.classes:
            label = existing_vl.get(cls["id"])
            if label is not None:
                existed["vertexlabels"].append(cls["id"])
                current = set(label.get("properties") or [])
                missing = [p for p in cls.get("properties", []) if p not in current]
                if missing:
                    try:
                        self.append_vertex_label_properties(cls["id"], missing)
                        updated["vertexlabels"].append(
                            {"name": cls["id"], "properties": missing})
                    except HugeGraphError as e:
                        errors.append({"kind": "vertexlabel-append",
                                       "name": cls["id"], "error": str(e)})
                continue
            try:
                self.create_vertex_label(cls)
                existing_vl[cls["id"]] = {"name": cls["id"],
                                          "properties": cls.get("properties", [])}
                created["vertexlabels"].append(cls["id"])
            except HugeGraphError as e:
                errors.append({"kind": "vertexlabel", "name": cls["id"],
                               "error": str(e)})

        for edge in self.semantic.physical_edges():
            label = existing_el.get(edge["name"])
            if label is not None:
                existed["edgelabels"].append(edge["name"])
                current = set(label.get("properties") or [])
                missing = [p for p in edge.get("properties", []) if p not in current]
                if missing:
                    try:
                        self.append_edge_label_properties(
                            edge["name"], missing, edge.get("sort_keys"))
                        updated["edgelabels"].append(
                            {"name": edge["name"], "properties": missing})
                    except HugeGraphError as e:
                        errors.append({"kind": "edgelabel-append",
                                       "name": edge["name"], "error": str(e)})
                continue
            try:
                self.create_edge_label(edge)
                existing_el[edge["name"]] = {"name": edge["name"],
                                             "properties": edge.get("properties", [])}
                created["edgelabels"].append(edge["name"])
            except HugeGraphError as e:
                errors.append({"kind": "edgelabel", "name": edge["name"],
                               "error": str(e)})

        for cls in self.semantic.classes:
            for field in cls.get("indexes", []):
                idx = f"idx_{cls['id'].lower()}_{field}"
                if idx in existing_il:
                    existed["indexlabels"].append(idx)
                    continue
                try:
                    self.create_index_label(idx, cls["id"], field)
                    existing_il.add(idx)
                    created["indexlabels"].append(idx)
                except HugeGraphError as e:
                    # 索引构建可能异步或字段类型不支持，作为可观测告警返回。
                    errors.append({"kind": "indexlabel", "name": idx,
                                   "error": str(e)})

        return {"created": created, "existed": existed, "updated": updated,
                "errors": errors,
                "ok": not any(e["kind"] not in ("indexlabel",)
                              for e in errors)}

    # ---------------------------------------------------------------- vertices
    @staticmethod
    def encode_id(asset_id: str) -> str:
        # HugeGraph 的 string ID 路径参数本身是 JSON 字符串，必须带引号。
        return quote(json.dumps(str(asset_id), ensure_ascii=False), safe="")

    def get_vertex(self, asset_id: str) -> Optional[dict]:
        try:
            return self.request("GET", self._url(f"graph/vertices/{self.encode_id(asset_id)}"))
        except HugeGraphError as e:
            if e.status == 404:
                return None
            raise

    def create_vertex(self, label: str, asset_id: str,
                      properties: Dict[str, Any]) -> dict:
        body = {"label": label, "id": asset_id, "properties": properties}
        return self.request("POST", self._url("graph/vertices"), body,
                            ok_statuses=(201, 202))

    def update_vertex(self, asset_id: str, label: str,
                      properties: Dict[str, Any]) -> dict:
        body = {"label": label, "properties": properties}
        return self.request(
            "PUT",
            self._url(f"graph/vertices/{self.encode_id(asset_id)}"),
            body, params={"action": "append"}, ok_statuses=(200, 201))

    def upsert_vertex(self, label: str, asset_id: str,
                      properties: Dict[str, Any]) -> dict:
        try:
            return self.create_vertex(label, asset_id, properties)
        except HugeGraphError as e:
            msg = str(e).lower()
            if e.status in (400, 409) and ("exist" in msg or "已存在" in msg):
                return self.update_vertex(asset_id, label, properties)
            raise

    def list_vertices(self, label: Optional[str] = None,
                      properties: Optional[dict] = None,
                      limit: int = 100, offset: int = 0) -> List[dict]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if label:
            params["label"] = label
        if properties:
            params["properties"] = json.dumps(properties, ensure_ascii=False)
        try:
            data = self.request("GET", self._url("graph/vertices"), params=params)
        except HugeGraphError as e:
            if e.status == 404:
                return []
            raise
        return data.get("vertices", [])

    def delete_vertex(self, asset_id: str, label: Optional[str] = None) -> None:
        self.request("DELETE", self._url(f"graph/vertices/{self.encode_id(asset_id)}"),
                     params={"label": label} if label else None,
                     ok_statuses=(204, 200))

    # ------------------------------------------------------------------ edges
    def add_edge(self, label: str, source_id: str, target_id: str,
                 properties: Dict[str, Any]) -> dict:
        # HugeGraph 1.7 的 EdgeAPI 使用 outV/inV 表示两端顶点。
        body = {"label": label, "outV": source_id, "inV": target_id,
                "properties": properties}
        return self.request("POST", self._url("graph/edges"), body,
                            ok_statuses=(201, 202))

    def list_edges(self, vertex_id: Optional[str] = None,
                   direction: str = "BOTH", label: Optional[str] = None,
                   properties: Optional[dict] = None,
                   limit: int = 100, offset: int = 0) -> List[dict]:
        params: Dict[str, Any] = {"direction": direction, "limit": limit,
                                  "offset": offset}
        if vertex_id:
            params["vertex_id"] = json.dumps(vertex_id, ensure_ascii=False)
        if label:
            params["label"] = label
        if properties:
            params["properties"] = json.dumps(properties, ensure_ascii=False)
        try:
            data = self.request("GET", self._url("graph/edges"), params=params)
        except HugeGraphError as e:
            if e.status == 404:
                return []
            raise
        return data.get("edges", [])

    # --------------------------------------------------------------- helpers
    @staticmethod
    def edge_endpoints(edge: dict) -> tuple:
        source = edge.get("source") or edge.get("outV")
        target = edge.get("target") or edge.get("inV")
        return source, target

    def all_vertices(self, limit_per_label: int = 500) -> List[dict]:
        rows: List[dict] = []
        for cls in self.semantic.classes:
            rows.extend(self.list_vertices(label=cls["id"], limit=limit_per_label))
        return rows