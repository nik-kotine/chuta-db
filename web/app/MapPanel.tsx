"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import styles from "./page.module.css";

export type SpatialPoint = {
  table: string;
  rid: [number, number];
  longitude: number;
  latitude: number;
  label: string;
  values: unknown[];
};

type MapPanelProps = { points: SpatialPoint[]; selectedTable?: string | null };

export default function MapPanel({ points, selectedTable }: MapPanelProps) {
  const mapElement = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!mapElement.current || mapRef.current) return;
    const map = L.map(mapElement.current, { zoomControl: true }).setView([-12.0464, -77.0428], 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);
    window.setTimeout(() => map.invalidateSize(), 0);
    return () => { map.remove(); mapRef.current = null; layerRef.current = null; };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();
    const visiblePoints = selectedTable ? points.filter((point) => point.table === selectedTable) : points;
    const bounds: L.LatLngExpression[] = [];
    visiblePoints.forEach((point) => {
      const position: L.LatLngExpression = [point.latitude, point.longitude];
      bounds.push(position);
      L.circleMarker(position, { radius: 7, color: "#b47a32", weight: 2, fillColor: "#58745e", fillOpacity: 0.85 })
        .bindPopup(`<strong>${point.label}</strong><br>${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}<br><small>${point.table}</small>`)
        .addTo(layer);
    });
    if (bounds.length) map.fitBounds(L.latLngBounds(bounds), { padding: [25, 25], maxZoom: 15 });
  }, [points, selectedTable]);

  return <div className={styles.mapFrame}><div ref={mapElement} className={styles.mapCanvas} />{!points.length && <div className={styles.mapEmpty}>No hay filas con columnas `lat`/`lon` o `latitude`/`longitude` todavía.</div>}</div>;
}