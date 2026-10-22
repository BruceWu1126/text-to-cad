// The surface models a look may light its surfaces by (`surfaceLook.js`'s `brdf`), each a
// program change three's physical material takes in `onBeforeCompile`: GLSL put after three's own
// lighting functions, which the lighting loop is then pointed at (`RE_Direct`,
// `RE_IndirectSpecular`), and a line after three's material setup. Nothing of three's chunks is
// edited, so a program that names no model is three's own.
//
// `occt` is OCCT 7.9's PBR raster program (`occt_phong-pbr`), which the engine's look of the
// regular modes is defined by (`cadgen/engine/render/look.py`). Where three's physical model
// differs from it, the model draws OCCT's:
//   - the roughness as given (at least 0.01), with no floor of 0.0525 and no roughness added for a
//     curved surface's screen-space normal change;
//   - per light, `((1 - F) (1 - metallic) base / pi + D G F / 4) * radiance * max(0, N.L)`: GGX `D`,
//     Schlick-GGX `G = 1 / ((N.V (1 - k) + k) (N.L (1 - k) + k))` with `k = (r + 1)^2 / 8`, exact
//     Schlick `F` at V.H from `F0 = mix(F0(ior), base, metallic)` -- three's height-correlated
//     Smith term and its multiple-scattering compensation are not drawn;
//   - from the environment `(F0 A + B) * prefiltered(R, r)` and
//     `(1 - F_r) (1 - metallic) base * irradiance / pi`, `F_r` Schlick at N.V from the same F0 toward
//     `max(1 - r, F0)`: single scattering, three's multiple-scattering term not drawn.
// What OCCT's raster has no term for stays three's, as the look has the viewer draw it: a clear
// coat's layer, anisotropy's lobe and transmission.

const OCCT_LIGHTING = /* glsl */ `
// OCCT 7.9's PBR program (surfaceBrdf.js).
float occtFresnel( const in float cosine ) {
	float x = 1.0 - cosine;
	float x2 = x * x;
	return x2 * x2 * x;
}

void RE_Direct_Occt( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	#ifdef USE_CLEARCOAT
		float dotNLcc = saturate( dot( geometryClearcoatNormal, directLight.direction ) );
		clearcoatSpecularDirect += dotNLcc * directLight.color * BRDF_GGX_Clearcoat( directLight.direction, geometryViewDir, geometryClearcoatNormal, material );
	#endif
	vec3 halfDir = normalize( directLight.direction + geometryViewDir );
	float dotVH = saturate( dot( geometryViewDir, halfDir ) );
	vec3 f0 = material.specularColorBlended;
	vec3 F = f0 + ( 1.0 - f0 ) * occtFresnel( dotVH );
	#ifdef USE_ANISOTROPY
		vec3 specular = BRDF_GGX( directLight.direction, geometryViewDir, geometryNormal, material );
	#else
		float dotNV = saturate( dot( geometryNormal, geometryViewDir ) );
		float dotNH = saturate( dot( geometryNormal, halfDir ) );
		float k = pow2( material.roughness + 1.0 ) * 0.125;
		float G = 1.0 / ( ( dotNV * ( 1.0 - k ) + k ) * ( dotNL * ( 1.0 - k ) + k ) );
		vec3 specular = D_GGX( pow2( material.roughness ), dotNH ) * G * F * 0.25;
	#endif
	reflectedLight.directSpecular += irradiance * specular;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseContribution ) * ( 1.0 - F );
}

void RE_IndirectSpecular_Occt( const in vec3 radiance, const in vec3 irradiance, const in vec3 clearcoatRadiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
	#ifdef USE_CLEARCOAT
		clearcoatSpecularIndirect += clearcoatRadiance * EnvironmentBRDF( geometryClearcoatNormal, geometryViewDir, material.clearcoatF0, material.clearcoatF90, material.clearcoatRoughness );
	#endif
	vec3 reflected = radiance;
	#if defined( ENVMAP_TYPE_CUBE_UV ) && ! defined( USE_ANISOTROPY )
		// The environment along the mirror direction itself (three bends it toward the normal).
		vec3 mirror = transformDirectionByInverseViewMatrix( reflect( - geometryViewDir, geometryNormal ), viewMatrix );
		reflected = textureCubeUV( envMap, envMapRotation * mirror, material.roughness ).rgb * envMapIntensity;
	#endif
	vec3 f0 = material.specularColorBlended;
	reflectedLight.indirectSpecular += reflected * ( f0 * material.dfg.x + material.dfg.y );
	float dotNV = saturate( dot( geometryNormal, geometryViewDir ) );
	vec3 fresnel = f0 + ( max( vec3( 1.0 - material.roughness ), f0 ) - f0 ) * occtFresnel( dotNV );
	reflectedLight.indirectDiffuse += ( 1.0 - fresnel ) * material.diffuseContribution * irradiance * RECIPROCAL_PI;
}

#undef RE_Direct
#define RE_Direct RE_Direct_Occt
#undef RE_IndirectSpecular
#define RE_IndirectSpecular RE_IndirectSpecular_Occt
`;

const OCCT_ROUGHNESS = /* glsl */ `
material.roughness = clamp( roughnessFactor, 0.01, 1.0 );
`;

function occtCompile(shader) {
  const after = (include, text) => {
    const found = `#include <${include}>`;
    if (!shader.fragmentShader.includes(found)) throw new Error(`the occt surface model needs three's ${found}`);
    shader.fragmentShader = shader.fragmentShader.replace(found, `${found}\n${text}`);
  };
  after("lights_physical_pars_fragment", OCCT_LIGHTING);
  after("lights_physical_fragment", OCCT_ROUGHNESS);
}

const occtKey = () => "surface-brdf:occt";

/** The models by name: what a material takes to be lit by one (`useSurfaceBrdf`). */
export const SURFACE_BRDFS = Object.freeze({
  occt: Object.freeze({ onBeforeCompile: occtCompile, customProgramCacheKey: occtKey })
});

/**
 * Light a physical material by the model `name` (`SURFACE_BRDFS`), or by three's own for none;
 * returns whether its program changes (the caller then sets `needsUpdate`).
 */
export function useSurfaceBrdf(THREE, material, name = null) {
  if (name != null && !Object.hasOwn(SURFACE_BRDFS, name)) {
    throw new Error(`a surface model is one of ${Object.keys(SURFACE_BRDFS).join(", ")}, not ${JSON.stringify(name)}`);
  }
  const model = name == null ? null : SURFACE_BRDFS[name];
  const onBeforeCompile = model ? model.onBeforeCompile : THREE.Material.prototype.onBeforeCompile;
  const customProgramCacheKey = model ? model.customProgramCacheKey : THREE.Material.prototype.customProgramCacheKey;
  if (material.onBeforeCompile === onBeforeCompile && material.customProgramCacheKey === customProgramCacheKey) return false;
  material.onBeforeCompile = onBeforeCompile;
  material.customProgramCacheKey = customProgramCacheKey;
  return true;
}
