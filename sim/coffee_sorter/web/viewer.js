import * as THREE from './vendor/three.module.js';
import {OrbitControls} from './vendor/OrbitControls.js';
import {RoomEnvironment} from './vendor/RoomEnvironment.js';

async function start() {
  const compressed = Uint8Array.from(atob(document.querySelector('#replay-data').textContent.trim()), c => c.charCodeAt(0));
  const decoded = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
  const data = JSON.parse(await new Response(decoded).text());
  const stage = document.querySelector('#stage');
  const renderer = new THREE.WebGLRenderer({antialias:true, alpha:false, powerPreference:'high-performance'});
  const gl=renderer.getContext(), debug=gl.getExtension('WEBGL_debug_renderer_info');
  const gpuName=debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):'unknown';
  const softwareRenderer=/swiftshader|llvmpipe|software/i.test(gpuName);
  renderer.setPixelRatio(softwareRenderer?.6:Math.min(window.devicePixelRatio,1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.needsUpdate = true;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = .95;
  renderer.domElement.setAttribute('aria-label', 'Coffee sorter simulation; drag to orbit and scroll to zoom');
  stage.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#e8eade');
  scene.fog = new THREE.Fog('#e8eade', 4, 12);
  const camera = new THREE.PerspectiveCamera(35, 1, .005, 30);
  camera.up.set(0,0,1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = .08;
  controls.minDistance = .12;
  controls.maxDistance = 5;
  controls.maxPolarAngle = Math.PI*.49;
  const room = new RoomEnvironment();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromScene(room, .04);
  scene.environment = env.texture;
  scene.environmentIntensity = .7;
  room.dispose(); pmrem.dispose();
  const key = new THREE.DirectionalLight('#fff5de', 2.4);
  key.position.set(-1.5,-2.5,4);
  key.castShadow = true;
  key.shadow.mapSize.set(1024,1024);
  Object.assign(key.shadow.camera,{left:-1.8,right:1.8,top:1.8,bottom:-1.8,near:.1,far:8});
  key.shadow.normalBias=.002;key.shadow.bias=-.0001;
  key.target.position.set(-.2,0,.4);
  scene.add(key,key.target,new THREE.HemisphereLight('#ffffed','#80937b',1));
  const fill = new THREE.DirectionalLight('#d6e8ff',1.1);fill.position.set(1.5,2,2);scene.add(fill);
  const pbr = (color, metalness=0, roughness=.5) => new THREE.MeshStandardMaterial({color,metalness,roughness});
  const materials = {
    steel:pbr('#a9b4b0',.8,.29), frame:pbr('#263c34',.65,.4),
    belt:pbr('#284c85',.12,.85), floor:new THREE.MeshLambertMaterial({color:'#dadfcf'}),
    chute:new THREE.MeshStandardMaterial({color:'#a9b8ae',metalness:.25,roughness:.2,transparent:true,opacity:.23,depthWrite:false,side:THREE.DoubleSide})
  };
  // A woven rubber normal pattern, generated locally without an image request.
  const texCanvas=document.createElement('canvas');texCanvas.width=texCanvas.height=64;
  const ctx=texCanvas.getContext('2d');ctx.fillStyle='#777';ctx.fillRect(0,0,64,64);
  ctx.fillStyle='#999';for(let y=0;y<64;y+=4)ctx.fillRect(0,y,64,1);
  const rubber = new THREE.CanvasTexture(texCanvas);rubber.wrapS=rubber.wrapT=THREE.RepeatWrapping;rubber.repeat.set(28,12);
  materials.belt.bumpMap=rubber;materials.belt.bumpScale=.00035;
  const machineMeshes=[];
  for(const g of data.machine){
    let geometry;
    if(g.type===6)geometry=g.name==='floor'?new THREE.BoxGeometry(40,40,.04):new THREE.BoxGeometry(...g.size.map(v=>2*v));
    else if(g.type===5){geometry=new THREE.CylinderGeometry(g.size[0],g.size[0],2*g.size[1],24);geometry.rotateX(Math.PI/2);}
    else continue;
    let material=materials[g.material];
    if(!material){
      material=pbr(new THREE.Color().setRGB(...g.rgba.slice(0,3)),.12,.5);
      if(g.rgba[1]>.5 && g.rgba[0]<.3)material=pbr('#648b51',.2,.5);
      if(g.rgba[0]>.6 && g.rgba[1]<.3)material=pbr('#a45f45',.2,.5);
      if(g.rgba[0]>.9 && g.rgba[1]>.9){material.emissive=new THREE.Color('#fff8d8');material.emissiveIntensity=1;}
    }
    const mesh=new THREE.Mesh(geometry,material);
    mesh.position.fromArray(g.pos);mesh.quaternion.set(g.quat[1],g.quat[2],g.quat[3],g.quat[0]);
    mesh.castShadow=g.name!=='floor'&&g.material!=='chute';mesh.receiveShadow=true;
    scene.add(mesh);machineMeshes.push(mesh);
  }
  const L=data.layout;
  // Additional visual supports for the camera bridge; no collision geometry is altered.
  for(const y of [-1,1]){
    const support=new THREE.Mesh(new THREE.BoxGeometry(.026,.026,.43),materials.frame);
    support.position.set(L.cam_x,y*(L.belt_w/2+.048),L.belt_z+.205);support.castShadow=true;scene.add(support);
  }
  const nozzleGeo=new THREE.CylinderGeometry(.0022,.0015,.016,10);nozzleGeo.rotateX(Math.PI/2);
  const nozzles=new THREE.InstancedMesh(nozzleGeo,materials.steel,L.n_nozzles);
  const dummy=new THREE.Object3D();
  for(let i=0;i<L.n_nozzles;i++){
    dummy.position.set(L.ej_x,-L.belt_w/2+L.belt_w/L.n_nozzles*(i+.5),L.belt_z+L.ej_z_offset);
    dummy.updateMatrix();nozzles.setMatrixAt(i,dummy.matrix);
  }
  nozzles.castShadow=true;scene.add(nozzles);
  const scan=new THREE.Mesh(new THREE.PlaneGeometry(L.cam_fov,L.belt_w),new THREE.MeshBasicMaterial({color:'#cae0d3',transparent:true,opacity:.13,depthWrite:false,side:THREE.DoubleSide}));
  scan.position.set(L.cam_x,0,L.belt_z+.0008);scene.add(scan);
  // Each class owns one draw call; per-bean pose and dimensions come from the export.
  const beanMetadata=new Map(data.beans.map(b=>[b[0],b]));
  const peakByClass=data.classes.map((_,c)=>Math.max(...data.frames.map(f=>{
    let count=0;for(let i=0;i<f.beans.length;i+=9)if(beanMetadata.get(f.beans[i])[1]===c)count++;return count;
  })));
  const beanMeshes=data.classes.map((spec,c)=>{
    const geom=spec.shape==='box'?new THREE.BoxGeometry(2,2,2):new THREE.SphereGeometry(1,12,8);
    if(spec.shape!=='box'){
      const pos=geom.attributes.position;
      for(let i=0;i<pos.count;i++){
        const x=pos.getX(i),y=pos.getY(i),z=pos.getZ(i);
        const grain=1+.026*Math.sin(x*21+y*17+z*29);
        const crease=z>0 ? .2*Math.exp(-y*y*120)*(1-x*x):0;
        pos.setXYZ(i,x*grain,y*grain,z*grain-crease);
      }
      geom.computeVertexNormals();
    }
    const mat=pbr(new THREE.Color().setRGB(...spec.rgb),0,.82);
    const mesh=new THREE.InstancedMesh(geom,mat,Math.max(1,peakByClass[c]));
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.frustumCulled=false;
    mesh.castShadow=false;mesh.receiveShadow=true;scene.add(mesh);return mesh;
  });
  const white=new THREE.Color('white'),rejectColor=new THREE.Color('#ff643c'),acceptColor=new THREE.Color('#a4ee89');
  const puffGeo=new THREE.SphereGeometry(1,8,6);
  const puffMat=new THREE.MeshBasicMaterial({color:'#a8f3ff',transparent:true,opacity:.12,depthWrite:false,blending:THREE.AdditiveBlending});
  const puffs=new THREE.InstancedMesh(puffGeo,puffMat,L.n_nozzles*3);puffs.frustumCulled=false;scene.add(puffs);
  // Soft additive sprites supply a small bloom-like halo around recorded air pulses.
  const glowCanvas=document.createElement('canvas');glowCanvas.width=glowCanvas.height=64;
  const glowCtx=glowCanvas.getContext('2d');const gradient=glowCtx.createRadialGradient(32,32,0,32,32,32);
  gradient.addColorStop(0,'rgba(178,245,255,.8)');gradient.addColorStop(.25,'rgba(103,216,255,.3)');gradient.addColorStop(1,'rgba(103,216,255,0)');
  glowCtx.fillStyle=gradient;glowCtx.fillRect(0,0,64,64);
  const glowMap=new THREE.CanvasTexture(glowCanvas);
  const glows=Array.from({length:L.n_nozzles},(_,i)=>{
    const s=new THREE.Sprite(new THREE.SpriteMaterial({map:glowMap,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending}));
    s.position.set(L.ej_x,-L.belt_w/2+L.belt_w/L.n_nozzles*(i+.5),L.belt_z+.005);s.scale.set(.045,.09,1);scene.add(s);return s;
  });
  const annotations=[
    ['01 / FEED',[-1.08,0,.86],'overview'],['02 / INSPECT',[L.cam_x,0,L.belt_z+.5],'overview'],
    ['03 / AIR JETS',[L.ej_x,.22,L.belt_z+.14],'sorting'],
    ['ACCEPT',[L.split_x+.30,-.27,L.belt_z-.19],'sorting'],['REJECT',[L.split_x+.04,-.27,L.belt_z-.39],'sorting']
  ].map(([text,pos,view])=>{const el=document.createElement('div');el.className='annotation';el.textContent=text;stage.append(el);return {el,pos:new THREE.Vector3(...pos),view};});
  const presets={overview:{position:[1.55,-2.2,1.75],target:[-.23,0,.5]},sorting:{position:[.40,-1.25,.79],target:[.16,0,.46]},inspection:{position:[-.20,-.15,1.75],target:[-.32,0,.6]}};
  let currentView='overview';
  function setCamera(name){const p=presets[name];currentView=name;camera.position.fromArray(p.position);controls.target.fromArray(p.target);controls.update();document.querySelectorAll('[data-camera]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.camera===name)));}
  document.querySelectorAll('[data-camera]').forEach(b=>b.onclick=()=>setCamera(b.dataset.camera));
  setCamera('overview');
  function resize(){const w=stage.clientWidth,h=stage.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.fov=THREE.MathUtils.radToDeg(2*Math.atan(Math.tan(THREE.MathUtils.degToRad(35)/2)*Math.max(1,1.2/camera.aspect)));camera.updateProjectionMatrix();}
  new ResizeObserver(resize).observe(stage);resize();
  const play=document.querySelector('#play'),scrub=document.querySelector('#scrub'),timeLabel=document.querySelector('#time');
  const speed=document.querySelector('#speed'),highlight=document.querySelector('#highlight');
  const through=document.querySelector('#through'),rejected=document.querySelector('#rejected'),lost=document.querySelector('#lost');
  const stats=document.querySelector('#stats');
  const duration=data.frames.at(-1).t;let t=Math.min(1,duration),playing=true;
  scrub.max=duration;
  function setPlaying(value){playing=value;play.textContent=value?'Pause':'Play';play.setAttribute('aria-label',value?'Pause replay':'Play replay');}
  play.onclick=()=>setPlaying(!playing);
  scrub.oninput=()=>{t=Number(scrub.value);setPlaying(false);};
  document.addEventListener('keydown',e=>{if(e.code==='Space'&&e.target===document.body){e.preventDefault();setPlaying(!playing);}});
  document.querySelector('#config').textContent=`${data.config.rate.toLocaleString()} beans/s · ${L.n_nozzles} jets · ${data.config.jetForce.toFixed(2)} N`;
  let frameIndex=0;
  const nextIndices=new Int32Array(Math.max(...data.beans.map(b=>b[0]))+1);
  const qa=new THREE.Quaternion(),qb=new THREE.Quaternion();
  const projected=new THREE.Vector3();
  let frameCount=0,fpsStart=performance.now(),fps=0,visible=0;
  let last=performance.now();
  const telemetry={ready:true,fps:0,visibleBeans:0,frame:0,time:0,renderCalls:0,renderer:gpuName, pixelRatio:renderer.getPixelRatio()};
  window.coffeeReplay={data,telemetry,seek(value){t=THREE.MathUtils.clamp(value,0,duration);setPlaying(false);},setCamera,setPlaying};
  function render(now){
    const dt=Math.min((now-last)/1000,.25);last=now;
    if(playing){t+=dt*Number(speed.value);if(t>duration)t=data.frames[0].t;}
    while(frameIndex<data.frames.length-1&&data.frames[frameIndex+1].t<=t)frameIndex++;
    while(frameIndex>0&&data.frames[frameIndex].t>t)frameIndex--;
    const f=data.frames[frameIndex],next=data.frames[Math.min(frameIndex+1,data.frames.length-1)];
    const alpha=next.t===f.t?0:THREE.MathUtils.clamp((t-f.t)/(next.t-f.t),0,1);
    nextIndices.fill(-1);for(let i=0;i<next.beans.length;i+=9)nextIndices[next.beans[i]]=i;
    const counts=data.classes.map(()=>0);
    for(let i=0;i<f.beans.length;i+=9){
      const uid=f.beans[i],meta=beanMetadata.get(uid),c=meta[1],j=nextIndices[uid];
      const a=f.beans,b=j<0?f.beans:next.beans,k=j<0?i:j;
      dummy.position.set(THREE.MathUtils.lerp(a[i+1],b[k+1],alpha)/10000,THREE.MathUtils.lerp(a[i+2],b[k+2],alpha)/10000,THREE.MathUtils.lerp(a[i+3],b[k+3],alpha)/10000);
      qa.set(a[i+5],a[i+6],a[i+7],a[i+4]).normalize();qb.set(b[k+5],b[k+6],b[k+7],b[k+4]).normalize();dummy.quaternion.copy(qa).slerp(qb,alpha);
      if(data.classes[c].shape==='capsule')dummy.scale.set(meta[3]/100000,meta[3]/100000,(meta[2]+meta[3])/100000);
      else dummy.scale.set(meta[2]/100000,meta[3]/100000,meta[4]/100000);
      dummy.updateMatrix();const mesh=beanMeshes[c],slot=counts[c]++;mesh.setMatrixAt(slot,dummy.matrix);
      mesh.setColorAt(slot,highlight.checked?(a[i+8]===2?rejectColor:a[i+8]===1?acceptColor:white):white);
    }
    beanMeshes.forEach((m,c)=>{m.count=counts[c];m.instanceMatrix.needsUpdate=true;if(m.instanceColor)m.instanceColor.needsUpdate=true;});
    visible=f.beans.length/9;
    const jetAges=new Float32Array(L.n_nozzles).fill(Infinity);
    for(const fire of data.fires){const age=t-fire[0];if(age>=0&&t<=fire[1]+.065)jetAges[fire[2]]=Math.min(jetAges[fire[2]],age);}
    let puffCount=0;
    for(let n=0;n<L.n_nozzles;n++){
      const age=jetAges[n],on=Number.isFinite(age);glows[n].visible=on;
      if(!on)continue;
      glows[n].material.opacity=.4*Math.max(0,1-age/.08);
      for(let k=0;k<3;k++){
        dummy.position.set(L.ej_x+age*.25,-L.belt_w/2+L.belt_w/L.n_nozzles*(n+.5),L.belt_z+L.ej_z_offset-.01-k*.025-age*.4);
        dummy.quaternion.identity();dummy.scale.set(.0025+age*.06,.0025+age*.06,.018+age*.07);dummy.updateMatrix();puffs.setMatrixAt(puffCount++,dummy.matrix);
      }
    }
    puffs.count=puffCount;puffs.instanceMatrix.needsUpdate=true;
    [through,rejected,lost].forEach((el,i)=>el.textContent=f.counters[i].toLocaleString());
    scrub.value=t;timeLabel.textContent=`${t.toFixed(2)} / ${duration.toFixed(2)} s`;
    controls.update();
    for(const a of annotations){
      projected.copy(a.pos).project(camera);
      const show=currentView!=='inspection'&&(currentView==='overview'||a.view==='sorting')&&projected.z<1&&Math.abs(projected.x)<.9&&Math.abs(projected.y)<.8;
      a.el.style.display=show?'block':'none';a.el.style.left=`${(projected.x*.5+.5)*stage.clientWidth}px`;a.el.style.top=`${(-projected.y*.5+.5)*stage.clientHeight}px`;
    }
    renderer.render(scene,camera);
    frameCount++;
    if(now-fpsStart>=1000){fps=frameCount*1000/(now-fpsStart);frameCount=0;fpsStart=now;stats.textContent=`${fps.toFixed(0)} fps · ${visible} visible · ${data.config.fps} fps source`;}
    Object.assign(telemetry,{fps,visibleBeans:visible,frame:frameIndex,time:t,renderCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles,activeJets:jetAges.filter(Number.isFinite).length,playing});
    requestAnimationFrame(render);
  }
  document.querySelector('#loading').remove();requestAnimationFrame(render);
}
start().catch(error=>{
  document.querySelector('#loading')?.remove();
  const el=document.createElement('div');el.id='error';el.setAttribute('role','alert');
  el.textContent=`The 3D replay could not start: ${error.message}. Use a current browser with WebGL 2 and hardware acceleration enabled.`;
  document.querySelector('#stage').append(el);console.error(error);
});
