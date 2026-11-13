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
| C1 | **Census**: which GPU features the game uses. The census code is removed again. | **Done** (results below) |
| C2 | **Plug-in skeleton**: `librexgpu-tt.so` (`stage2/renderer/`), loaded through the SDK's `gpu_plugin` seam when the start screen says **Renderer: New**. It forwards every call to the SDK renderer, so New must look and run exactly like Old; the test is only that it builds, loads and plays. | **Done**: Thor, Turnip Gen8 V39, 720p, FXAA, Renderer New: plug-in loaded, 60 FPS, GPU ~80-89 % busy @ 680 MHz, same as Old |
| T1 | **GPU time per pass**: timestamps at every render-pass begin and end (`gpu_time.txt` in the logs, one block per 10 s). Draws exactly as before. | **Done** (results below) |
| T2 | **GPU time per shader pair**: a timestamp in front of every draw. | **Done** (results below) |
| T3 | **Heavy shaders**: a temporary **Test** row hides each of the three most expensive pixel shaders in turn (screenshot shows what it draws, `gpu_time.txt` what it costs) and can turn the alpha test off for the heaviest. All shaders' microcode listings go to `shaders.txt`. | **Build ready, waiting for logs** |
| C3 | Own command processor in the plug-in: reads the game's command stream itself, shows the picture through its own swap path | — |
| C4 | Render targets as plain Vulkan images (no EDRAM), resolves as image copies | — |
| C5 | Shaders translated once to lean SPIR-V with real vertex inputs; vertex/index data in real buffers | — |
| C6 | Textures, effects, menus; compare every venue against Old | — |

Each step keeps the **Renderer: Old / New** row, so anything can be compared with the SDK renderer.

Why T1 comes before C3-C6: the C2 logs show the emulator's GPU thread at only ~48 % of one core
on the Thor, nearly half of it asleep, and its biggest single cost is memory page protection
(9 %), not render-target emulation. So the remaining load is on the GPU itself, and until now
its split was only estimated by switching things off. T1 measures it directly.

### Testing T3

Build and install the test app (command above). Then, for each **Test** setting below: set it on
the start screen, start a match, take one screenshot (L3 + R3) during a rally, play about a
minute, quit. Export the logs once at the end (each run is kept separately).

1. Off
2. Hide shader A
3. A without alpha test
4. Hide shader B
5. Hide shader C

Missing or odd-looking things in 2-5 are expected; that is what the screenshots are for.

## T2 result: the expensive shaders

Same device and settings as T1, in a match (16.3 ms of GPU work per frame with the extra
timestamps). The two main-scene passes (8.3 ms in T1) split like this:

| Pixel shader | GPU time | Draws per frame | What it is (from the census) |
|---|---|---|---|
| A `5C7A942F` | ~3.5 ms (4 vertex shaders) | ~70, ~100,000 vertices | 13 textures, alpha test on, depth test and write |
| B `2CF2B765` | ~1.3 ms (4 vertex shaders) | ~22, ~45,000 vertices | 21 textures |
| C `8A141C27` | 0.7 ms | 8, 3 vertices each (full-screen) | 4 textures, depth test and write |
| ~20 others | ~5 ms together | ~450 | none above 0.7 ms |

The "gap" T1 booked after a main-scene pass was really part of pass #4's draws (shader A); the
GPU waits only ~0.9 ms per frame in total.

Shader A matters most. With the alpha test on, the shader can throw pixels away, so the GPU
cannot reject hidden pixels by depth before running it (every overlapping pixel is shaded with
all 13 textures). The SDK already uses early depth rejection for every draw without alpha
test.

## T1 result: where the GPU's frame goes

AYN Thor (Adreno 740, Turnip Gen8 V39), 720p, FXAA, Hidden passes Skipped, in a match at 60 FPS
(16.7 ms per frame), 2026-11-13. Typical 10-second block:

| Part of the frame | GPU time | Notes |
|---|---|---|
| Main scene, the two 1280-wide passes | 8.3 ms | ~400 draws, ~275,000 vertices, begun 9-10 times per frame |
| One gap after a main-scene pass, once per frame | 3.3 ms | No draws. Probably mostly the GPU waiting for the game's next batch of commands (T2 separates this) |
| Copies after the main-scene passes | 1.3 ms | 6-7 full-screen copies per frame |
| Effect buffers (640 / 320 / 160 wide), all passes together | ~1.5 ms | |
| Shadow buffer (1440 wide) | ~0.3 ms | |
| Present (FXAA, gamma, to the screen) | 0.3 ms | |
| GPU waiting between frames | 0.7 ms | |

So the main scene's ~400 draws are half of the frame and the first target; effects, shadows and
the present step are small.

## C1 result: what the game asks of the GPU

AYN Thor (Adreno 740, Turnip Gen8 V39), 720p, FXAA, Hidden passes Skipped, 460 s session
(menus, character select, matches), 2026-11-13.

| Area | What the game uses | What it means for an own renderer |
|---|---|---|
| Shaders | 56 vertex shaders (29 with one vertex stream, 27 with two), 67 pixel shaders (0-21 textures; 2 write depth) | Small enough to translate and check one by one |
| Vertex data | 5 vertex layouts in total | Real vertex inputs are practical (no generic fetch code in every shader) |
| Geometry | Triangle strips (about 80 % of draws, mostly indexed), triangle lists, triangle fans, rectangle lists, quad lists | Fans, rectangles and quads need converting; strips and lists are native |
| Textures | 60 kinds: DXT1 / DXT3 / DXT5 with mip chains, 8888, the game's own render results, 24-bit depth read back as a texture, two cube/volume lookups | Standard; DXT needs decoding where the GPU lacks it (already done by the SDK) |
| Render targets | 18 setups: 1280-wide main scene with depth, half / quarter / eighth / sixteenth-size effect buffers (some 4x anti-aliased), two depth-only shadow buffers (1280 and 1440 wide), one two-target case | A short, fixed list: each can be a plain image |
| Copies out of render targets | 18 kinds: colour to 8888 textures at 1280x720 down to 80x48, depth to depth textures at 1280x720, 1120x704, 640x480, 1440x1280 | All are plain image copies (what F17/F18 already do) |
| Memory export (GPU writing game memory) | Not used | The hardest Xbox 360 feature is not needed |
| EDRAM re-use | 32 kinds of "same memory, different buffer" hand-overs, tens of thousands of times each | This is what the SDK renderer emulates at a cost. F12 has skipped all of them for many builds with no visible difference, so an own renderer can ignore them |

Conclusion: the game is a regular, forward-rendered title with a fixed set of effect buffers.
Nothing in the census needs the Xbox 360's EDRAM to be imitated.
