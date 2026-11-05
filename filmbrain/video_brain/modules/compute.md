# Finish quickly while keeping the computer responsive

Reduce redundant work first, use bounded background resources, and treat GPU admission separately from installed drivers.

## Go faster by avoiding redundant production

Cache by content and configuration; rerender the changed beat and its real dependents. Reuse source geometry, fonts and scene templates while preserving documentary asset nonreuse. Produce an early representative preview.

Faster work does not require maximizing every CPU. Avoided re-recording and re-rendering can save more time than extra workers on a saturated machine.

**Check:** A change-impact manifest explains why each task ran or was reused. A reused artifact matches its complete current identity.

**Evidence:** CH16657, CH16724, CX3567, CX3572.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Keep video workers in the protected background pool

Use the already-tested background_video.py launcher: BelowNormal, background logical CPUs 4,5,6,7,8,9,14,15 (0xc3f0), bounded library threads and windowless process creation. Keep the terminal pool 0,1,2,3,10,11,12,13 (0x3c0f) available to the UI.

These masks are specific to this machine and topology. Affinity does not isolate shared RAM, disk, thermals or GPU composition, and a thread hint is not a hard total CPU quota.

**Check:** Read current guard policy and native child affinity/priority. Stop adding load when the user reports freezes, even if one instantaneous metric looks comfortable.

**Evidence:** CX3567, memory:nvidia_59706_installed_dual_video_gpu_cpu_policy_2026_09_05.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Actively use the GPU for suitable production stages

The owner wants every video team using suitable GPU acceleration and targets 80 percent utilization (September 5, 19:38 Jerusalem). Concurrent NVENC encoding and useful CUDA or Blender work may run when they fit current VRAM, physical RAM and commit budgets. Retire the one-exclusive-GPU-job convention: each producer records a disjoint topic:gpu-workload/<hostname>/<agent-id> claim. Use NVENC through FFmpeg 7.1.1 or the verified PyAV 18.1 route. Keep exact mathematical construction on its appropriate renderer.

The 80 percent target is useful production demand, not an idle stress test, a guaranteed speedup or permission to ignore memory pressure. GPU compute and dedicated encoder utilization are different counters. Do not rerender completed media merely to raise a counter. CUDA availability is repaired; sustained driver stability remains under observation. Stop and preserve evidence on Display 4101 or nvlddmkm faults.

**Check:** Record actual input/output, renderer or encoder, device, PID plus birth, sampled compute/encode utilization, VRAM and end-to-end usable output per time. Check recent RAM/commit low-water headroom before model staging; unload a cancelled model at a safe boundary. Preserve BelowNormal background workers, CPU mask 0xc3f0 and responsive terminals. Reduce load on actual UI, thermal or memory pressure.

**Evidence:** CX3567, owner_update:2026-09-05T09:26:46Z, owner-update:2026-09-05-active-gpu-use, owner-update:1788626329242, memory:owner_order_use_80_percent_of_the_gpu_everyone_2026_09_05.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Budget peaks, not a single comfortable sample

Allow for a co-tenant model's peak RAM and commit use, monitor a recent window, and leave the UI headroom. Avoid reload storms and duplicate model servers. Pause the newest optional work first during actual pressure.

ComfyUI previously swung about 20 GB between samples, defeating a one-shot free-memory guard. A peer collision was also mistaken for a driver failure.

**Check:** Before a heavy stage and during it, inspect current telemetry and ownership. A stale or incomplete required signal is not permission to scale up.

**Evidence:** scar:MISTAKES_2026-09-03_memory_guard, scar:MISTAKES_2026-09-04_duplicate_film.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.

## Measure a representative cost before scaling

Time the same small representative scene with candidate settings, including warm-up, encoding and transfer. Compare completed usable output per elapsed time and observed resource pressure. Separate initialization from steady work.

A fast failed render or a warm-up-contaminated benchmark can produce a false lesson. Do not turn a single sample into a reliable two-hour production forecast.

**Check:** The receipt includes scene identity, settings, valid output, wall time and pressure. Keep estimates as ranges until more observations exist.

**Evidence:** CH16657, CH9087, memory:filmbrain_the_movie_making_part_of_the_brain_2026_09_04.

**Hint:** Apply the cited owner preference; operational techniques are starting points until their named checks have actual evidence.
