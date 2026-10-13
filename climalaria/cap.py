"""Build and validate Common Alerting Protocol (CAP 1.2) messages.

Covers message structure, required elements, enumerations, geographic targeting
(polygon + geocode) and the alert lifecycle (Alert -> Update -> Cancel via <references>).
Spec: OASIS CAP v1.2, http://docs.oasis-open.org/emergency/cap/v1.2/
"""
from __future__ import annotations

import datetime as dt
import uuid

from lxml import etree

NS = "urn:oasis:names:tc:emergency:cap:1.2"
ENUMS = {
    "status": {"Actual", "Exercise", "System", "Test", "Draft"},
    "msgType": {"Alert", "Update", "Cancel", "Ack", "Error"},
    "scope": {"Public", "Restricted", "Private"},
    "category": {"Geo", "Met", "Safety", "Security", "Rescue", "Fire", "Health", "Env", "Transport", "Infra", "CBRNE", "Other"},
    "responseType": {"Shelter", "Evacuate", "Prepare", "Execute", "Avoid", "Monitor", "Assess", "AllClear", "None"},
    "urgency": {"Immediate", "Expected", "Future", "Past", "Unknown"},
    "severity": {"Extreme", "Severe", "Moderate", "Minor", "Unknown"},
    "certainty": {"Observed", "Likely", "Possible", "Unlikely", "Unknown"},
}
REQUIRED_ALERT = ["identifier", "sender", "sent", "status", "msgType", "scope"]
REQUIRED_INFO = ["category", "event", "urgency", "severity", "certainty"]


def _ts(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone(dt.timedelta(hours=3))).replace(microsecond=0).isoformat()


def polygon_string(geom, max_points: int = 60) -> str:
    """CAP polygon: space-separated 'lat,lon' pairs, closed (first == last), >= 4 points."""
    poly = max(geom.geoms, key=lambda p: p.area) if geom.geom_type == "MultiPolygon" else geom
    tol = 0.0
    coords = list(poly.exterior.coords)
    while len(coords) > max_points:
        tol += 0.01
        coords = list(poly.simplify(tol, preserve_topology=True).exterior.coords)
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return " ".join(f"{lat:.4f},{lon:.4f}" for lon, lat in coords)


def build_alert(*, sender: str, event: str, category: str, urgency: str, severity: str, certainty: str,
                headline: str, description: str, instruction: str, area_desc: str, polygon: str,
                geocode: tuple[str, str], msg_type: str = "Alert", status: str = "Exercise",
                response_type: str = "Monitor", references: list[str] | None = None,
                expires_days: int = 30, sent: dt.datetime | None = None, identifier: str | None = None,
                web: str | None = None) -> etree._Element:
    sent = sent or dt.datetime.now(dt.timezone.utc)
    E = lambda tag, text=None, parent=None: _el(tag, text, parent)
    alert = etree.Element(f"{{{NS}}}alert", nsmap={None: NS})
    E("identifier", identifier or f"UG-EWS-{uuid.uuid4()}", alert)
    E("sender", sender, alert)
    E("sent", _ts(sent), alert)
    E("status", status, alert)
    E("msgType", msg_type, alert)
    E("scope", "Public", alert)
    if references:
        E("references", " ".join(references), alert)
    info = E("info", None, alert)
    E("language", "en-UG", info)
    E("category", category, info)
    E("event", event, info)
    E("responseType", response_type, info)
    E("urgency", urgency, info)
    E("severity", severity, info)
    E("certainty", certainty, info)
    E("expires", _ts(sent + dt.timedelta(days=expires_days)), info)
    E("senderName", "Uganda National Situation Room (demo)", info)
    E("headline", headline, info)
    E("description", description, info)
    E("instruction", instruction, info)
    if web:
        E("web", web, info)
    area = E("area", None, info)
    E("areaDesc", area_desc, area)
    E("polygon", polygon, area)
    gc = E("geocode", None, area)
    E("valueName", geocode[0], gc)
    E("value", geocode[1], gc)
    return alert


def _el(tag, text, parent):
    el = etree.SubElement(parent, f"{{{NS}}}{tag}") if parent is not None else etree.Element(f"{{{NS}}}{tag}")
    if text is not None:
        el.text = text
    return el


def reference_of(alert: etree._Element) -> str:
    """'sender,identifier,sent' string used in <references> by Update/Cancel messages."""
    f = lambda t: alert.findtext(f"{{{NS}}}{t}")
    return f"{f('sender')},{f('identifier')},{f('sent')}"


def validate(alert: etree._Element) -> list[str]:
    """Return a list of problems (empty list = valid)."""
    errs = []
    f = lambda node, t: node.findtext(f"{{{NS}}}{t}")
    for t in REQUIRED_ALERT:
        if not f(alert, t):
            errs.append(f"missing <{t}>")
    for t in ("status", "msgType", "scope"):
        v = f(alert, t)
        if v and v not in ENUMS[t]:
            errs.append(f"<{t}> invalid value '{v}'")
    if f(alert, "msgType") in ("Update", "Cancel") and not f(alert, "references"):
        errs.append("Update/Cancel must carry <references>")
    for info in alert.findall(f"{{{NS}}}info"):
        for t in REQUIRED_INFO:
            if not f(info, t):
                errs.append(f"info missing <{t}>")
        for t in ("category", "urgency", "severity", "certainty", "responseType"):
            for el in info.findall(f"{{{NS}}}{t}"):
                if el.text not in ENUMS[t]:
                    errs.append(f"<{t}> invalid value '{el.text}'")
        for area in info.findall(f"{{{NS}}}area"):
            if not f(area, "areaDesc"):
                errs.append("area missing <areaDesc>")
            for poly in area.findall(f"{{{NS}}}polygon"):
                pts = poly.text.split()
                if len(pts) < 4:
                    errs.append("polygon needs >= 4 points")
                if pts[0] != pts[-1]:
                    errs.append("polygon not closed (first point must equal last)")
                for p in pts:
                    lat, lon = map(float, p.split(","))
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        errs.append(f"coordinate out of range: {p}")
                        break
    return errs


def to_xml(alert: etree._Element) -> bytes:
    return etree.tostring(alert, xml_declaration=True, encoding="UTF-8", pretty_print=True)
