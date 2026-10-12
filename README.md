# Glock 17 – 3D-Modell

Interaktives 3D-Anschauungsmodell einer Glock 17 (Gen 4) im Maßstab 1:1, aufgebaut aus Grundkörpern mit [three.js](https://threejs.org).
Modelliert ist nur die Außenform, ohne Innenmechanik.

## Dateien

| Datei | Inhalt |
| --- | --- |
| `index.html` | 3D-Viewer: drehen, zoomen, Ansichten (3/4, links, rechts, oben), Rahmenfarbe (Schwarz, Coyote, Oliv, Grau), Teilenamen per Hover/Antippen |
| `glock17.glb` | Das gleiche Modell als glTF-Binärdatei (in Metern, 17 benannte Teile, ca. 16 000 Dreiecke) |

## Ansehen

- **Viewer:** `index.html` im Browser öffnen (braucht Internet für three.js vom CDN).
  Falls der Browser lokale Dateien blockiert: `npx serve .` und dann `http://localhost:3000` öffnen.
- **GLB-Datei:** lässt sich direkt in Blender (*Datei → Import → glTF 2.0*), im Windows-3D-Viewer
  oder auf <https://gltf-viewer.donmccurdy.com> öffnen.

## Aufbau des Modells

Alle Maße im Code sind Millimeter. Die Teile entstehen aus extrudierten Seitenprofilen
(`THREE.ExtrudeGeometry` mit Fasen); Auswurffenster, Griffrillen am Schlitten, Laufbohrung und
Querschlitz der Zubehörschiene werden per CSG ([three-bvh-csg](https://github.com/gkjohnson/three-bvh-csg))
herausgeschnitten. Die Griffstruktur ist eine prozedurale Bump-Map.

| Maß | Vorbild (ca.) | Modell |
| --- | --- | --- |
| Länge | 202 mm | 202 mm |
| Höhe | 138 mm | 136 mm |
| Breite | 32 mm | 31 mm |
| Lauflänge | 114 mm | 114 mm |

## GLB neu exportieren

Nach Änderungen am Modell im geöffneten Viewer in der Browser-Konsole ausführen:

```js
await pistolModel.downloadGLB()
```
