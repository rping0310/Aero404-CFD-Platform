// Presentation adapter: preserve platform() APIs, authentication and permissions.
const platformCore = window.platform;
window.platform = function () {
  const app = platformCore();
  const paths={jobs:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 11h18M8 3v4m8-4v4"/>',cases:'<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8m-8 4h8m-8 4h5"/>',slices:'<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',plots:'<path d="M4 3v17h17M7 15l4-5 4 2 5-7"/>','3d':'<path d="m12 2 9 5v10l-9 5-9-5V7l9-5Zm0 10 9-5M12 12 3 7m9 5v10"/>',settings:'<circle cx="12" cy="12" r="4"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2"/>',about:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',gallery:'<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>'};
  const order=['jobs','cases','slices','plots','3d','gallery','settings','about'];
  app.tabs.sort((a,b)=>order.indexOf(a.id)-order.indexOf(b.id));
  app.tabs.forEach(t=>{t.icon=`<svg viewBox="0 0 24 24" aria-hidden="true">${paths[t.id]||paths.about}</svg>`;});
  const originalInit = app.init;
  const originalRefreshJobs = app.refreshJobs;
  const originalLogin = app.login;
  const originalLogout = app.logout;
  return Object.assign(app, {
    glassMode: 'smoke', glassBlur: 1, glassStrength: 85,
    glassMotion: true, glassEnabled: true, glassPhoto: 'alpine', dockPinned: true,
    glassPhotos: [{id:'alpine',name:'Alpine lake',url:'/static/img/alpine-lake.jpg'}],
    glassUploadBusy: false, galleryError: '', sharedGallerySignature: '',
    async init() {
      try {
        const p = JSON.parse(localStorage.getItem('aero404-platform-glass') || '{}');
        for (const k of ['glassMode','glassBlur','glassStrength','glassMotion','glassEnabled','dockPinned']) if (k in p) this[k] = p[k];
        this.applyGlass();
      } catch (e) { console.warn('Local appearance preferences unavailable', e); }
      await originalInit.call(this);
      if (!this.user) this.resetLoginAppearance();
    },
    resetLoginAppearance() {
      this.glassMode='smoke';this.glassEnabled=true;this.glassMotion=true;
      this.applyGlass();
    },
    async login() {
      this.resetLoginAppearance();
      await originalLogin.call(this);
    },
    async logout() {
      await originalLogout.call(this);
      this.resetLoginAppearance();
    },
    async refreshJobs() {
      await originalRefreshJobs.call(this);
      await this.loadSharedGallery();
    },
    applySharedGallery(data) {
      const signature=JSON.stringify(data);
      if(signature===this.sharedGallerySignature)return;
      this.sharedGallerySignature=signature;this.glassPhotos=data.photos;this.glassPhoto=data.selected;
      this.applyGlass();
    },
    async loadSharedGallery() {
      if(!this.user)return;
      try {this.applySharedGallery(await this.api('/api/gallery'));this.galleryError='';}
      catch(e){this.galleryError='Could not load shared backgrounds: '+e.message;}
    },
    async chooseGlassPhoto(id) {
      if(this.user?.role!=='admin')return;
      try {
        this.applySharedGallery(await this.api('/api/gallery/selection',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}));
        this.galleryError='';
      }catch(e){this.galleryError=e.message;}
    },
    galleryBackground() {
      const p=this.glassPhotos.find(p=>p.id===this.glassPhoto)||this.glassPhotos[0];
      return `url(${JSON.stringify(p.url)})`;
    },
    applyGlass() {
      if (!['smoke','light','dark'].includes(this.glassMode)) this.glassMode='smoke';
      document.body.classList.toggle('theme-dark',this.glassMode!=='light');
      document.body.classList.toggle('theme-smoke',this.glassMode==='smoke');
      document.body.classList.toggle('theme-black',this.glassMode==='dark');
      document.body.classList.toggle('reduced-glass',!this.glassEnabled);
      document.body.classList.toggle('no-motion',!this.glassMotion);
      document.body.classList.toggle('dock-pinned',this.dockPinned);
      document.dispatchEvent(new Event('dock-preference-change'));
      document.getElementById('photo-wallpaper').style.backgroundImage=this.galleryBackground();
      const photo=this.glassPhotos.find(p=>p.id===this.glassPhoto)||this.glassPhotos[0];
      photoAccent(photo.url);
      document.documentElement.style.setProperty('--photo-blur',`${Math.max(0,Math.min(24,Number(this.glassBlur)||0))}px`);
      document.documentElement.style.setProperty('--photo-strength',Math.max(.2,Math.min(1,Number(this.glassStrength)/100)));
      try { localStorage.setItem('aero404-platform-glass',JSON.stringify(Object.fromEntries(['glassMode','glassBlur','glassStrength','glassMotion','glassEnabled','dockPinned'].map(k=>[k,this[k]])))); } catch {}
    },
    cycleGlassMode() { const m=['smoke','light','dark'];this.glassMode=m[(m.indexOf(this.glassMode)+1)%3];this.applyGlass(); },
    async importGlassPhoto(event) {
      const file=event.target.files[0]; event.target.value=''; if (!file) return;
      if(this.user?.role!=='admin'||this.glassUploadBusy)return;
      if (!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>25*1024*1024) {this.flash('Choose a JPG, PNG or WebP photo smaller than 25 MB.');return;}
      this.glassUploadBusy=true;
      try {
        const form=new FormData();form.append('file',file);
        this.applySharedGallery(await this.api('/api/gallery/photos',{method:'POST',body:form}));
        this.galleryError='';this.flash('Shared background saved on the server.');
      } catch(e) {this.galleryError='Could not save photo: '+e.message;}
      finally {this.glassUploadBusy=false;}
    }
  });
};
let accentSource='',accentEpoch=0;
function photoAccent(url) {
  if(url===accentSource)return;accentSource=url;const epoch=++accentEpoch;
  document.documentElement.style.setProperty('--photo-hue','220');
  const image=new Image();image.onload=()=>{
    if(epoch!==accentEpoch)return;
    try{
      const c=document.createElement('canvas');c.width=48;c.height=48;
      const ctx=c.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0,48,48);
      const data=ctx.getImageData(0,0,48,48).data,bins=new Map();
      for(let i=0;i<data.length;i+=4){const rgb=[data[i],data[i+1],data[i+2]],hi=Math.max(...rgb),lo=Math.min(...rgb);
        if(hi-lo<30||hi<50||lo>220)continue;const key=rgb.map(v=>v>>5).join(',');
        const b=bins.get(key)||{sum:[0,0,0],n:0};b.n++;rgb.forEach((v,k)=>b.sum[k]+=v);bins.set(key,b);
      }
      const b=[...bins.values()].sort((a,b)=>b.n-a.n)[0];if(!b)return;
      const [r,g,bl]=b.sum.map(v=>v/b.n),max=Math.max(r,g,bl),min=Math.min(r,g,bl),d=max-min;
      let h=max===r?((g-bl)/d)%6:max===g?(bl-r)/d+2:(r-g)/d+4;
      document.documentElement.style.setProperty('--photo-hue',String((h*60+360)%360));
    }catch{}
  };image.src=url;
}

document.addEventListener('DOMContentLoaded', () => {
  const dock=document.querySelector('.sidebar');
  const desktop=matchMedia('(min-width:761px)');let point;
  const pinned=()=>document.body.classList.contains('dock-pinned');
  const inZone=()=>{const top=(innerHeight-dock.offsetHeight)/2;return point&&point.x>=0&&point.x<=dock.offsetWidth+4&&point.y>=top&&point.y<=top+dock.offsetHeight;};
  const setVisible=open=>{
    if(document.body.classList.contains('dock-hidden')===!open)return;
    document.body.classList.toggle('dock-hidden',!open);
    document.dispatchEvent(new Event('dock-visibility'));
  };
  const show=()=>setVisible(true);
  const hide=()=>{if(desktop.matches&&!pinned()&&!inZone()&&!dock.querySelector(':focus-visible'))setVisible(false);};
  const syncPin=()=>{if(pinned()||!desktop.matches)show();else hide();};
  syncPin();
  document.addEventListener('dock-preference-change',syncPin);
  desktop.addEventListener('change',syncPin);
  document.addEventListener('pointermove',e=>{if(!desktop.matches||e.pointerType==='touch')return;point={x:e.clientX,y:e.clientY};if(inZone()){if(e.clientX<=innerWidth*.01)show();}else hide();});
  dock.addEventListener('focusin',show);dock.addEventListener('focusout',()=>queueMicrotask(hide));
  dock.addEventListener('keydown',e=>{if(e.key==='Escape'&&!pinned()){document.activeElement.blur();point=null;hide();document.querySelector('.titlebar button')?.focus();}});
  const resize=new ResizeObserver(()=>{for(const id of ['plot-main','plot-sub']){const host=document.getElementById(id);if(host?.data&&host.offsetWidth)Plotly.Plots.resize(host);}});
  document.querySelectorAll('.plot-host').forEach(e=>resize.observe(e));
  // Optics initializes only after a logged-in shell is visible.
  const shell=document.querySelector('.app-shell');
  const start=()=>{if(shell.offsetWidth&&!shell.hasAttribute('x-cloak')){observer.disconnect();import('/static/js/glass-overlay.js?v=perf6').catch(console.warn);}};
  const observer=new MutationObserver(start);observer.observe(shell,{attributes:true});start();
});
