// three.js + three-gpu-pathtracer (WebGPU) candidate: the GLB loads natively (every
// KHR_materials_* extension, TANGENTs, the clip through AnimationMixer); the shot file drives the
// camera per frame. Each frame accumulates `spp` samples, then the linear float image is read back
// (half floats, base64) for the shared post: OIDN + Blender's OCIO AgX grade.
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';
import { WebGPUPathTracer } from 'three-gpu-pathtracer/webgpu';
import { PhysicalCamera } from 'three-gpu-pathtracer';

let renderer, pathTracer, scene, camera, mixer, shot, W, H;

const lin = ( c ) => new THREE.Color().setRGB( c[ 0 ], c[ 1 ], c[ 2 ], THREE.LinearSRGBColorSpace );
const rel = ( p, dir ) => '/' + dir + '/' + p.split( '/' + dir + '/' )[ 1 ];

function applyFrame( f ) {

	const fr = shot.frames[ f ];
	if ( mixer && fr.clip_t != null ) mixer.setTime( fr.clip_t );
	scene.updateMatrixWorld( true );
	camera.position.set( ...fr.pos );
	camera.up.set( 0, 1, 0 );
	camera.lookAt( ...fr.target );
	camera.setFocalLength( fr.focal_mm );
	camera.fStop = fr.fstop;
	camera.focusDistance = fr.focus;
	camera.updateProjectionMatrix();
	camera.updateMatrixWorld( true );

}

window.setup = async ( shotUrl, scale, frameBudget, envRotSign ) => {

	const t0 = performance.now();
	shot = await ( await fetch( shotUrl ) ).json();
	W = Math.round( shot.width * scale );
	H = Math.round( shot.height * scale );
	renderer = new THREE.WebGPURenderer( { antialias: false } );
	renderer.setPixelRatio( 1 );
	renderer.setSize( W, H );
	renderer.toneMapping = THREE.AgXToneMapping; // what the canvas shows; the output is read linear
	document.body.appendChild( renderer.domElement );
	await renderer.init();

	scene = new THREE.Scene();
	const gltf = await new GLTFLoader().loadAsync( rel( shot.glb, 'glb' ) );
	scene.add( gltf.scene );
	const env = await new HDRLoader().loadAsync( rel( shot.env, 'env' ) );
	env.mapping = THREE.EquirectangularReflectionMapping;
	scene.environment = env;
	scene.environmentIntensity = shot.env_strength ?? 1;
	scene.environmentRotation.set( 0, envRotSign * THREE.MathUtils.degToRad( shot.env_rot || 0 ), 0 );
	scene.background = lin( shot.background );

	const box = new THREE.Box3().setFromObject( gltf.scene );
	const size = box.getSize( new THREE.Vector3() ).length();
	const c = box.getCenter( new THREE.Vector3() );
	const fl = shot.floor;
	const floor = new THREE.Mesh(
		new THREE.PlaneGeometry( size * 60, size * 60 ).rotateX( - Math.PI / 2 ),
		new THREE.MeshPhysicalMaterial( { color: lin( fl.color ), roughness: fl.roughness, metalness: fl.metallic || 0 } ),
	);
	floor.position.set( c.x, fl.y, c.z );
	scene.add( floor );

	if ( shot.clip ) {

		const clip = gltf.animations.find( ( a ) => a.name === shot.clip );
		if ( clip ) {

			mixer = new THREE.AnimationMixer( gltf.scene );
			mixer.clipAction( clip ).play();

		}

	}

	camera = new PhysicalCamera( 30, W / H, size * 0.002, size * 400 );
	camera.filmGauge = 36;
	applyFrame( 0 );

	pathTracer = new WebGPUPathTracer( renderer );
	pathTracer.renderDelay = 0;
	pathTracer.fadeDuration = 0;
	pathTracer.minSamples = 0;
	pathTracer.dynamicLowRes = false;
	pathTracer.frameBudget = frameBudget;
	const ts = performance.now();
	pathTracer.setScene( scene, camera );
	let tris = 0;
	const mats = new Set();
	scene.traverse( ( o ) => {

		if ( o.isMesh ) {

			tris += ( o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count ) / 3;
			mats.add( o.material.type );

		}

	} );
	return { setup_ms: Math.round( performance.now() - t0 ), bvh_ms: Math.round( performance.now() - ts ), W, H, tris,
		materials: [ ...mats ], clips: gltf.animations.map( ( a ) => a.name ), clip_played: !! mixer };

};

window.renderFrame = async ( f, spp ) => {

	const t0 = performance.now();
	applyFrame( f );
	if ( mixer ) pathTracer.updateTransforms();
	pathTracer.updateCamera();
	pathTracer.reset();
	let calls = 0, counts = { min: 0, avg: 0 };
	while ( true ) {

		pathTracer.renderSample();
		calls ++;
		if ( calls % 4 === 0 ) {

			counts = await pathTracer.getSampleCountsAsync();
			if ( counts.avg >= spp ) break;

		}

	}

	const tr = performance.now();
	const data = await renderer.backend.copyTextureToBuffer( pathTracer.target, 0, 0, W, H );
	const px = W * H;
	const per = data.length / px;
	const rgb = new Float16Array( px * 3 );
	for ( let i = 0; i < px; i ++ ) {

		rgb[ i * 3 ] = data[ i * per ];
		rgb[ i * 3 + 1 ] = data[ i * per + 1 ];
		rgb[ i * 3 + 2 ] = data[ i * per + 2 ];

	}

	const b64 = await new Promise( ( res ) => {

		const r = new FileReader();
		r.onload = () => res( r.result.slice( r.result.indexOf( ',' ) + 1 ) );
		r.readAsDataURL( new Blob( [ rgb.buffer ] ) );

	} );
	return { b64, W, H, calls, samples: counts.min, avg: counts.avg, channels: per, ctor: data.constructor.name,
		trace_ms: Math.round( tr - t0 ), readback_ms: Math.round( performance.now() - tr ) };

};

window.ready = true;
