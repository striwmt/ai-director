# LUTs

Vendor LUTs are not distributed with this repository (license restrictions).
Download official LUTs from the camera vendor and place the `.cube` files here.

Expected by `config/color_profiles.yaml` (rename as needed):

- `DJI_DLog2_to_Rec709.cube`
- `DJI_DLog_to_Rec709.cube`
- `DJI_DLogM_to_Rec709.cube`

If a LUT is missing, analysis proxies fall back to a neutral tone adjustment
and a warning is logged. The LUT file hash is recorded with every analysis
result for cache invalidation.
