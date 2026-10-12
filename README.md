# Glock 17 – 3D-Modell

Interaktives 3D-Anschauungsmodell einer Glock 17 (Gen 4) in Originalmaßen, aufgebaut mit [three.js](https://threejs.org).
Modelliert ist nur die Außenform, ohne Innenmechanik.

## Dateien

| Datei | Inhalt |
| --- | --- |
| `index.html` | 3D-Viewer: drehen, zoomen, Ansichten (3/4, links, rechts, oben), Rahmenfarbe (Schwarz, Coyote, Oliv, Grau), Bemaßung ein/aus, Teilenamen per Hover/Antippen |
| `glock17.glb` | Das gleiche Modell als glTF-Binärdatei (in Metern, 18 benannte Teile, ca. 19 000 Dreiecke, mit Griffstruktur als Normal-Map) |

## Ansehen

- **Viewer:** `index.html` im Browser öffnen (braucht Internet für three.js vom CDN).
  Falls der Browser lokale Dateien blockiert: `npx serve .` und dann `http://localhost:3000` öffnen.
- **GLB-Datei:** lässt sich direkt in Blender (*Datei → Import → glTF 2.0*), im Windows-3D-Viewer
  oder auf <https://gltf-viewer.donmccurdy.com> öffnen.

## Maße

Der Schalter „Maße“ blendet Maßlinien ein. Die Werte werden beim Laden direkt am Modell gemessen
(Bounding-Box bzw. Strahl-Treffer), nicht fest eingetragen.

| Maß | Vorbild (ca.) | Modell |
| --- | --- | --- |
| Gesamtlänge | 202 mm | 202 mm |
| Höhe mit Magazin | 138 mm | 138 mm |
| Breite | 32 mm | 32 mm |
| Schlitten | 186 × 25,5 mm | 186 × 25,5 mm |
| Lauflänge | 114 mm | 114 mm |
| Visierlinie | 165 mm | 165 mm |
| Abzugsabstand | 72 mm | 72 mm |

## Details

- **Schlitten:** 45°-Fasen oben, gefaste Front und Rückseite, 9 Griffrillen je Seite, Auswurffenster
  mit schräger Vorderkante, Auszieher in eigener Vertiefung, Kerbe für den Schlittenfanghebel,
  Gravur „17 Gen4 / AUSTRIA 9x19“ links.
- **Lauf:** Laufhaube und Patronenlager im Auswurffenster, sechseckiges Polygonprofil, Mündungskrone.
- **Visierung:** Kimme mit weißer U-Markierung, Korn mit weißem Punkt (6,1 mm hoch).
- **Rahmen:** Zubehörschiene mit Querschlitz, Rahmenplatte, geriffelte Front am Abzugsbügel,
  Gen-4-Griffstruktur (1-mm-Pyramiden), Fingermulden, Beavertail, Griffmulde am Magazinschacht,
  vergrößerter Magazinhalter, Zerlegehebel mit Riffelung, drei Haltestifte.
- **Abzug:** Safe-Action-Abzug mit Sicherungszunge.

## Aufbau

Alle Maße im Code sind Millimeter. Die Teile entstehen aus extrudierten Profilen
(`THREE.ExtrudeGeometry` mit Fasen); Auswurffenster, Rillen, Bohrungen und Kerben werden per CSG
([three-bvh-csg](https://github.com/gkjohnson/three-bvh-csg)) herausgeschnitten. Die Griffstruktur
ist eine prozedural erzeugte Normal-Map. Auf Geräten mit Maus rendert der Viewer zusätzlich
Ambient Occlusion (GTAO) für Schatten in Vertiefungen.

## GLB neu exportieren

Nach Änderungen am Modell im geöffneten Viewer in der Browser-Konsole ausführen:

```js
await pistolModel.downloadGLB()
```
