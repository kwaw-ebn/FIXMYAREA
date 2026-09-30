import React,{useEffect,useState} from 'react';
type Call=(path:string,opts?:RequestInit,token?:string)=>Promise<any>;
export type Job={id:string;account:string;kind:'report'|'inspection';label:string;fields:Record<string,string>;photo?:Blob;created:string};
const DATABASE='fixmyarea-device-v1';
async function database():Promise<IDBDatabase>{return new Promise((resolve,reject)=>{const r=indexedDB.open(DATABASE,1);r.onupgradeneeded=()=>{r.result.createObjectStore('jobs',{keyPath:'id'});r.result.createObjectStore('tasks',{keyPath:'id'})};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(new Error('Device storage is unavailable.'))})}
async function transact<T>(store:string,mode:IDBTransactionMode,action:(s:IDBObjectStore)=>IDBRequest<T>):Promise<T>{const db=await database();return new Promise((resolve,reject)=>{const tx=db.transaction(store,mode);const r=action(tx.objectStore(store));tx.oncomplete=()=>{db.close();resolve(r.result)};tx.onerror=tx.onabort=()=>{db.close();reject(new Error('Device storage failed. Check available space.'))}})}
const changed=()=>window.dispatchEvent(new Event('fma-device-change'));
export async function jobs(account:string):Promise<Job[]>{return (await transact<Job[]>('jobs','readonly',s=>s.getAll())).filter(j=>j.account===account).sort((a,b)=>a.created.localeCompare(b.created))}
export async function enqueue(job:Job){const pending=await jobs(job.account);if(pending.length>=20)throw new Error('Synchronize your 20 pending items before saving another.');await transact('jobs','readwrite',s=>s.put(job));changed()}
async function remove(id:string){await transact('jobs','readwrite',s=>s.delete(id));changed()}
export async function compressPhoto(file:File):Promise<Blob>{
 if(file.size>20_000_000)throw new Error('Choose a photo smaller than 20 MB.');
 const source=URL.createObjectURL(file);
 try{const img=await new Promise<HTMLImageElement>((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('This photo format cannot be read. Choose JPEG or PNG.'));image.src=source});const scale=Math.min(1,1600/Math.max(img.width,img.height));const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.width*scale));canvas.height=Math.max(1,Math.round(img.height*scale));const ctx=canvas.getContext('2d');if(!ctx)throw new Error('Photo processing unavailable.');ctx.drawImage(img,0,0,canvas.width,canvas.height);return await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Photo compression failed.')),'image/jpeg',0.78))}finally{URL.revokeObjectURL(source)}
}
let syncing=false;
export async function synchronize(account:string,token:string,request:Call,notify:(m:string)=>void){
 if(syncing||!account||!token||!navigator.onLine)return;
 syncing=true;
 try{
  // Always validate the active account before uploading its saved items.
  const user=await request('/api/me',{},token);if(user.id!==account)throw new Error('Sign into the account that saved these items.');
  for(const job of await jobs(account)){if(localStorage.getItem('fma_token')!==token)break;const body=new FormData();Object.entries(job.fields).forEach(([k,v])=>body.set(k,v));body.set('client_id',job.id);if(job.photo)body.set('photo',job.photo,'evidence.jpg');const result=await request(job.kind==='report'?'/api/reports':'/api/field/evidence',{method:'POST',body},token);await remove(job.id);notify(job.kind==='report'?`Report submitted: ${result.tracking}`:'Field inspection synchronized.');window.dispatchEvent(new Event('fma-synchronized'))}
 }catch(e){notify('Saved items remain on this device. Synchronization: '+String(e))}finally{syncing=false}
}
export function DeviceQueue({account,token,request,message}:{account:string;token:string;request:Call;message:(s:string)=>void}){
 const [items,setItems]=useState<Job[]>([]),[online,setOnline]=useState(navigator.onLine);
 useEffect(()=>{let active=true;const load=()=>jobs(account).then(x=>{if(active)setItems(x)}).catch(e=>message(String(e)));const sync=()=>{setOnline(navigator.onLine);synchronize(account,token,request,message)};const changed=()=>{load();sync()};load();sync();window.addEventListener('online',sync);window.addEventListener('offline',sync);window.addEventListener('fma-device-change',changed);return()=>{active=false;window.removeEventListener('online',sync);window.removeEventListener('offline',sync);window.removeEventListener('fma-device-change',changed)}},[account,token]);
 if(!account)return null;
 return <aside className="panel"><h2>Saved on this device</h2><p>{online?'Connected':'Offline'} · {items.length} pending. Saved items are submitted when this app is open and connectivity returns. Tracking numbers are issued after synchronization.</p>{items.map(j=><p key={j.id}>{j.kind==='report'?'Report':'Inspection'}: {j.label} · {new Date(j.created).toLocaleString()} <button className="link" onClick={()=>{if(window.confirm('Discard this unsynchronized item and its photo?'))remove(j.id).catch(e=>message(String(e)))}}>Discard</button></p>)}<button className="secondary" onClick={()=>synchronize(account,token,request,message)} disabled={!online||!items.length}>Synchronize saved items</button><small>Use a trusted device. Sign out keeps pending items for your account; clearing browser data deletes them.</small></aside>
}
export function FieldCapture({account,report,message}:{account:string;report:any;message:(s:string)=>void}){
 const [busy,setBusy]=useState(false);
 async function save(e:React.FormEvent<HTMLFormElement>){e.preventDefault();const form=e.currentTarget;const data=new FormData(form);setBusy(true);try{if(!account)throw new Error('Sign in online once before saving an inspection.');const file=data.get('photo') as File;const photo=file?.size?await compressPhoto(file):undefined;await enqueue({id:crypto.randomUUID(),account,kind:'inspection',label:report.tracking,fields:{report_id:report.id,note:String(data.get('note')||'')},photo,created:new Date().toISOString()});form.reset();message('Inspection and photo saved on this device. Open Saved on this device to synchronize.')}catch(e){message(String(e))}finally{setBusy(false)}}
 return <form className="panel" onSubmit={save}><h3>Save field inspection</h3><p>Capture notes and a photo with poor connectivity. Evidence stays internal to your organization. Status and public resolution changes require an online review.</p><label>Inspection findings<textarea name="note" minLength={5} maxLength={1000} required/></label><label>Inspection photo (optional)<input name="photo" type="file" accept="image/*" capture="environment"/></label><button className="primary" disabled={busy}>{busy?'Saving…':'Save inspection on device'}</button></form>
}
export async function saveTask(account:string,report:any){await transact('tasks','readwrite',s=>s.put({id:account+':'+report.id,account,report}));changed()}
export async function savedTasks(account:string){return (await transact<any[]>('tasks','readonly',s=>s.getAll())).filter(t=>t.account===account).map(t=>t.report)}
export function DownloadedTasks({account,open,message}:{account:string;open:(r:any)=>void;message:(s:string)=>void}){const [items,setItems]=useState<any[]>([]);useEffect(()=>{savedTasks(account).then(setItems).catch(e=>message(String(e)))},[account]);return <article className="panel"><h2>Downloaded field tasks</h2><p>Saved task details are available offline. Map tiles and public photos require a connection.</p>{items.map(r=><button className="reportcard" key={r.id} onClick={()=>open(r)}>{r.tracking} · {r.community}</button>)}{!items.length&&<p>Open an assigned report online and choose Save task for offline visits.</p>}</article>}

export function FieldEvidence({report,request,token,api,message}:{report:any;request:Call;token:string;api:string;message:(s:string)=>void}){
 const [items,setItems]=useState<any[]>([]),[image,setImage]=useState('');
 useEffect(()=>{let active=true;const load=()=>request(`/api/reports/${report.id}/field-evidence`,{},token).then(x=>{if(active)setItems(x)}).catch(()=>{});load();window.addEventListener('fma-synchronized',load);return()=>{active=false;window.removeEventListener('fma-synchronized',load)}},[report.id,token]);
 useEffect(()=>()=>{if(image)URL.revokeObjectURL(image)},[image]);
 async function view(id:string){try{const response=await fetch(`${api}/api/field/evidence/${id}/photo`,{headers:{Authorization:`Bearer ${token}`}});if(!response.ok)throw new Error('Photo access unavailable. Reconnect and check your organization access.');setImage(URL.createObjectURL(await response.blob()))}catch(e){message(String(e))}}
 return <article className="panel"><h3>Private field evidence</h3>{items.map(i=><div key={i.id}><p>{i.note} · {new Date(i.at).toLocaleString()}</p>{i.has_photo&&<button className="secondary" onClick={()=>view(i.id)}>View inspection photo</button>}</div>)}{!items.length&&<p>No synchronized field evidence.</p>}{image&&<><img src={image} alt="Private field inspection evidence" style={{maxWidth:'100%'}}/><button className="link" onClick={()=>setImage('')}>Hide photo</button></>}</article>
}
