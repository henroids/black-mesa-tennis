# Renderer branch

This branch is the start of an own renderer for the game (the NFS Carbon Android port's route:
read the game's GPU command stream and draw it directly with Vulkan, instead of imitating the
Xbox 360's GPU and its EDRAM). The `main` branch is the playable version and isn't affected.

The branch builds a **separate test app**, "Table Tennis (renderer test)"
(`org.tabletennis.recomp.renderertest`, `TableTennis-renderer-test.apk`), so it installs next to
the normal app. It has its own settings, game disc choice, imported drivers and logs.

Build it:

    cd ~/black-mesa-tennis && git fetch && git checkout renderer && git pull && bash stage2/tt_android_stage2.sh

Go back to the normal app's build:

    cd ~/black-mesa-tennis && git checkout main && git pull && bash stage2/tt_android_stage2.sh

## Steps

| Step | What | State |
|---|---|---|
| C1 | **Census**: which GPU features the game uses (`census.txt` in the logs, rewritten every 10 s): primitive types, unique shaders and their features, vertex formats, sampled texture formats, render-target setups, EDRAM ownership transfers, resolves, memexport. Draws exactly as the normal app. | Waiting for logs |
| C2 | Renderer plug-in skeleton (loads through the SDK's `gpu_plugin` seam, hands everything to the existing renderer) | — |
| C3+ | Shaders, images/textures, main scene, effects, menus; each behind an Old/New renderer choice | — |
