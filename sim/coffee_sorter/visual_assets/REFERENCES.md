# Visual references and licenses

Accessed 2026-09-19. All delivered meshes and shader graphs are original procedural work. No reference photographs, image textures, purchased assets, HDRIs or third-party meshes are bundled. The references inform shape and surface appearance; they do not constitute measured material calibration or photogrammetry.

| Reference | License / reuse decision | Observed features used |
| --- | --- | --- |
| Shih-Yu Chen, Chuan-Yu Chang, Cheng-Syue Ou and Chou-Tien Lien (2020), *Detection of Insect Damage in Green Coffee Beans Using VIS-NIR Hyperspectral Imaging*, Remote Sensing 12(15), 2348, Figure 4, p. 5. https://doi.org/10.3390/rs12152348 ; publisher https://www.mdpi.com/2072-4292/12/15/2348 ; PDF https://mdpi-res.com/d_attachment/remotesensing/remotesensing-12-02348/article_deploy/remotesensing-12-02348.pdf | CC BY 4.0, explicitly printed on PDF p. 34: https://creativecommons.org/licenses/by/4.0/ . Figure inspected as a reference; neither image nor PDF copied into this package. | Pale olive/cream healthy bean with curved central furrow; rough mottled dark bean; actual voids in insect-damaged bean; missing material and exposed interior on broken bean. |
| Espresso Academy, *Green Coffee Defects: Complete Guide with Examples and SCA Table*, 9 October 2025. https://espressoacademy.it/en/guide-en/green-coffee-defects-complete-guide-with-examples-and-sca-table/ | No clear reuse license found. Text descriptions only; photographs not copied, traced or used as textures. | Darkened black beans, perforated insect damage, broken/chipped class distinction. |
| Existing simulator profiles and half-bean source at base 4dbc350: `../profiles.py`, `../assets.py`, `../scene.py`, `../web/replay.json` | Existing repository source, retained unchanged. | Metre units, semi-axis ranges, mesh frame and conveyor geometry. Authoritative coordinate reference, not a source of realistic texture. |

## Tool provenance

Blender official download index: https://download.blender.org/release/Blender4.5/ . Blender is GPL software (https://www.blender.org/about/license/); the program itself is installed outside the repository. Blender's GPL does not automatically license artwork made with it. This package adds no third-party asset licensing obligation; it follows the repository's applicable terms.

## Limits

These are four illustrative prototypes. Biological variation, defect severity, and green-bean colour vary by origin and processing. The visual assets are presentation-only and must never replace inspection-camera materials or provide classifier validation evidence.
