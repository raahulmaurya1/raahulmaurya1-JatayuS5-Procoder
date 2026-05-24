import React, { useRef, useEffect } from 'react';

/**
 * GLBViewer
 * React port of the Three.js GLB viewer with custom model scaling,
 * rule-based gold mesh materials (with Glow), camera-facing logo, and hover interactions.
 */
export default function GLBViewer() {
  const mountRef = useRef(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    let animFrameId;
    let cleanupFn;

    Promise.all([
      import('three'),
      import('three/examples/jsm/loaders/GLTFLoader.js')
      // Note: OrbitControls removed because we are manually animating to isolate the box
    ]).then(([THREE, { GLTFLoader }]) => {
      if (!mountRef.current) return;

      const w = mount.clientWidth;
      const h = mount.clientHeight;

      /* ── Setup Scene, Camera, Renderer ── */
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 1000);
      camera.position.set(0, 1.6, 3);
      camera.lookAt(0, 1, 0); // Statically look at the center

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setSize(w, h);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0x000000, 0); 
      mount.appendChild(renderer.domElement);

      /* ── Lighting ── */
      scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.5));
      const dirLight = new THREE.DirectionalLight(0xffffff, 2);
      dirLight.position.set(5, 10, 5);
      scene.add(dirLight);

      /* ── Variables for Animation & Interaction ── */
      let mixer;
      let logoMesh;
      let loadedModel; // Reference to the full group
      let outerParts = []; // The meshes we WANT to rotate
      let innerBoxParts = []; // The meshes we want to keep STILL
      
      const clock = new THREE.Clock();
      
      // Hover State logic
      let isHovered = false;
      let currentSpeed = 0.2; // Base rotation speed
      const targetSpeedHover = 1.5; // Fast speed on hover
      const targetSpeedNormal = 0.2; // Normal speed

      /* ── Mouse Hover Tracking ── */
      const raycaster = new THREE.Raycaster();
      const mouse = new THREE.Vector2();

      const onMouseMove = (event) => {
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      };
      window.addEventListener('mousemove', onMouseMove);

      /* ── Gold Rules Function (Modified for accurate isolation & glow) ── */
      const applyGoldRules = (model, size) => {
        const gold = new THREE.Color("#cdaa20");
        const meshes = [];

        // Calculate absolute center to accurately isolate the box
        const mainBox = new THREE.Box3().setFromObject(model);
        const mainCenter = mainBox.getCenter(new THREE.Vector3());
        const mainSize = mainBox.getSize(new THREE.Vector3());
        const maxDim = Math.max(mainSize.x, mainSize.y, mainSize.z);

        model.traverse((child) => {
          if (child.isMesh) {
            const meshBox = new THREE.Box3().setFromObject(child);
            const meshSize = meshBox.getSize(new THREE.Vector3());
            
            const mCenter = meshBox.getCenter(new THREE.Vector3());
            const mMaxDim = Math.max(meshSize.x, meshSize.y, meshSize.z);
            
            // STRICT ISOLATION: Must be near the absolute center AND not be a huge mesh
            const distFromCenter = mCenter.distanceTo(mainCenter);
            const isInnerBox = distFromCenter < (maxDim * 0.3) && mMaxDim < (maxDim * 0.5);
            
            if (isInnerBox) {
              innerBoxParts.push(child);
            } else {
              outerParts.push(child); // Everything else (globe, rings, base) rotates!
            }

            meshes.push({
              mesh: child,
              y: meshBox.min.y,
              size: meshSize
            });
          }
        });

        meshes.sort((a, b) => a.y - b.y);

        const skipMeshes = [
          meshes[0]?.mesh,
          meshes[1]?.mesh,
          meshes[2]?.mesh
        ];

        meshes.forEach((item) => {
          if (skipMeshes.includes(item.mesh)) return;

          const s = item.size;
          const isCone = s.y > s.x * 0.8 && s.y > s.z * 0.8;
          if (isCone) return;

          const isSphere = Math.abs(s.x - s.y) < 0.1 && Math.abs(s.y - s.z) < 0.1 && s.x > size.x * 0.3;
          if (isSphere) return;

          if (item.y < size.y * 0.25 && s.y < size.y * 0.1) {
            item.mesh.material = new THREE.MeshStandardMaterial({
              color: gold,
              emissive: gold,          // Adds the glowing base color
              emissiveIntensity: 0.8,  // Controls how bright the glow is
              metalness: 0.5,
              roughness: 0.35
            });
          }
        });
      };

      /* ── Logo Function ── */
      const addLogo = (centerY) => {
        const textureLoader = new THREE.TextureLoader();
        const logoTexture = textureLoader.load("/logo.svg");

        const material = new THREE.MeshBasicMaterial({
          map: logoTexture,
          transparent: true,
          depthWrite: false
        });

        const geometry = new THREE.PlaneGeometry(0.8, 0.8);
        logoMesh = new THREE.Mesh(geometry, material);
        logoMesh.position.set(0, centerY, 0); 
        scene.add(logoMesh);
      };

      /* ── Load Model ── */
      const loader = new GLTFLoader();
      
      loader.load(
        "/scene.glb", 
        (gltf) => {
          const model = gltf.scene;
          loadedModel = model;
          scene.add(model);

          const box = new THREE.Box3().setFromObject(model);
          const size = box.getSize(new THREE.Vector3());
          const center = box.getCenter(new THREE.Vector3());

          model.position.x -= center.x;
          model.position.z -= center.z;
          model.position.y = -box.min.y + 0.5;

          const maxDim = Math.max(size.x, size.y, size.z);
          model.scale.setScalar(2.87 / maxDim);

          model.updateMatrixWorld(true);

          const finalBox = new THREE.Box3().setFromObject(model);
          const exactCenterY = finalBox.getCenter(new THREE.Vector3()).y;

          applyGoldRules(model, size);
          
          const manualOffset = 0.25; // Adjusted slightly based on your image
          addLogo(exactCenterY + manualOffset);

          if (gltf.animations.length) {
            mixer = new THREE.AnimationMixer(model);
            gltf.animations.forEach((clip) => {
              mixer.clipAction(clip).play();
            });
          }
        }
      );

      /* ── Animation Loop ── */
      const animate = () => {
        animFrameId = requestAnimationFrame(animate);

        const delta = clock.getDelta();
        if (mixer) mixer.update(delta);
        
        // --- Raycast for Hover ---
        if (loadedModel) {
          raycaster.setFromCamera(mouse, camera);
          const intersects = raycaster.intersectObject(loadedModel, true);
          isHovered = intersects.length > 0;
        }

        // --- Smooth Speed Transition ---
        const targetSpeed = isHovered ? targetSpeedHover : targetSpeedNormal;
        // Lerp the speed for smooth acceleration/deceleration
        currentSpeed += (targetSpeed - currentSpeed) * 0.1;

        // --- Selective Rotation ---
        // We only rotate the outer parts, leaving the innerBoxParts stationary
        outerParts.forEach(mesh => {
           // Rotate around the Y axis
           // Note: You may need to adjust the axis or pivot point depending on how the GLB was built
           mesh.rotation.y += currentSpeed * delta; 
        });

        // Make logo always face camera (it's stationary anyway, but good practice)
        if (logoMesh) {
          logoMesh.lookAt(camera.position);
        }

        renderer.render(scene, camera);
      };
      animate();

      /* ── Resize Handler ── */
      const handleResize = () => {
        if (!mount) return;
        const nw = mount.clientWidth;
        const nh = mount.clientHeight;
        camera.aspect = nw / nh;
        camera.updateProjectionMatrix();
        renderer.setSize(nw, nh);
      };
      window.addEventListener('resize', handleResize);

      /* ── Cleanup ── */
      cleanupFn = () => {
        window.removeEventListener('resize', handleResize);
        window.removeEventListener('mousemove', onMouseMove);
        cancelAnimationFrame(animFrameId);
        renderer.dispose();
        if (mount.contains(renderer.domElement)) {
          mount.removeChild(renderer.domElement);
        }
      };
    });

    return () => {
      cancelAnimationFrame(animFrameId);
      if (cleanupFn) cleanupFn();
    };
  }, []);

  return (
    <div
      ref={mountRef}
      style={{
        width: '100%',
        height: '100vh',
        cursor: 'pointer', // Changed to pointer to indicate hoverability
        overflow: 'hidden',
        background: 'transparent'
      }}
    />
  );
}