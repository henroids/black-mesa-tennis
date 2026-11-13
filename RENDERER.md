# Renderer branch

This branch is the start of an own renderer for the game (the NFS Carbon Android port's route:
read the game's GPU command stream and draw it directly with Vulkan, instead of imitating the
Xbox 360's GPU and its EDRAM). The `main` branch is the playable version and isn't affected.

The branch builds a **separate test app**, "Table Tennis (renderer test)"
(`org.tabletennis.recomp.renderertest`, `TableTennis-renderer-test.apk`), so it installs next to
the normal app. Its app icon is the normal one turned upside down (handles up). It has its own settings, game disc choice, imported drivers and logs.

Build it:

    cd ~/black-mesa-tennis && git fetch && git checkout renderer && git pull && bash stage2/tt_android_stage2.sh

Go back to the normal app's build:

    cd ~/black-mesa-tennis && git checkout main && git pull && bash stage2/tt_android_stage2.sh

## Steps

| Step | What | State |
|---|---|---|
| C1 | **Census**: which GPU features the game uses (`census.txt` in the logs, rewritten every 10 s). Draws exactly as the normal app. | **Done** (results below) |
| C2 | **Plug-in skeleton**: `librexgpu-tt.so` (`stage2/renderer/`), loaded through the SDK's `gpu_plugin` seam when the start screen says **Renderer: New**. It forwards every call to the SDK renderer, so New must look and run exactly like Old; the test is only that it builds, loads and plays. | **Build ready, waiting for logs** |
| C3 | Own command processor in the plug-in: reads the game's command stream itself, shows the picture through its own swap path | — |
| C4 | Render targets as plain Vulkan images (no EDRAM), resolves as image copies | — |
| C5 | Shaders translated once to lean SPIR-V with real vertex inputs; vertex/index data in real buffers | — |
| C6 | Textures, effects, menus; compare every venue against Old | — |

Each step keeps the **Renderer: Old / New** row, so anything can be compared with the SDK renderer.

### Testing C2

1. Build and install the test app (command above).
2. Start screen: **Renderer → New**. Play a few points, then quit.
3. Export the logs. The game log should contain `tt-renderer: new renderer plug-in loaded`.

## C1 result: what the game asks of the GPU

AYN Thor (Adreno 740, Turnip Gen8 V39), 720p, FXAA, Hidden passes Skipped, 460 s session
(menus, character select, matches), 2026-11-13.

| Area | What the game uses | What it means for an own renderer |
|---|---|---|
| Shaders | 56 vertex shaders (29 with one vertex stream, 27 with two), 67 pixel shaders (0-21 textures; 2 write depth) | Small enough to translate and check one by one |
| Vertex data | 5 vertex layouts in total | Real vertex inputs are practical (no generic fetch code in every shader) |
| Geometry | Triangle lists (most draws, mostly indexed), triangle fans, triangle strips, rectangle lists, quad lists | Rectangles and quads need converting; the rest is native |
| Textures | 60 kinds: DXT1 / DXT3 / DXT5 with mip chains, 8888, the game's own render results, 24-bit depth read back as a texture, two cube/volume lookups | Standard; DXT needs decoding where the GPU lacks it (already done by the SDK) |
| Render targets | 18 setups: 1280-wide main scene with depth, half / quarter / eighth / sixteenth-size effect buffers (some 4x anti-aliased), two depth-only shadow buffers (1280 and 1440 wide), one two-target case | A short, fixed list: each can be a plain image |
| Copies out of render targets | 18 kinds: colour to 8888 textures at 1280x720 down to 80x48, depth to depth textures at 1280x720, 1120x704, 640x480, 1440x1280 | All are plain image copies (what F17/F18 already do) |
| Memory export (GPU writing game memory) | Not used | The hardest Xbox 360 feature is not needed |
| EDRAM re-use | 32 kinds of "same memory, different buffer" hand-overs, tens of thousands of times each | This is what the SDK renderer emulates at a cost. F12 has skipped all of them for many builds with no visible difference, so an own renderer can ignore them |

Conclusion: the game is a regular, forward-rendered title with a fixed set of effect buffers.
Nothing in the census needs the Xbox 360's EDRAM to be imitated.
