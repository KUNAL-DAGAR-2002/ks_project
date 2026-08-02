import * as SecureStore from 'expo-secure-store';

export const API_URL=(process.env.EXPO_PUBLIC_API_URL||'https://kirana-saathi-api.onrender.com/api').replace(/\/$/,'');
const TOKEN_KEY='kirana_saathi_access_token';

export async function getToken(){return SecureStore.getItemAsync(TOKEN_KEY)}
export async function setToken(token:string|null){if(token)await SecureStore.setItemAsync(TOKEN_KEY,token,{keychainAccessible:SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY});else await SecureStore.deleteItemAsync(TOKEN_KEY)}

export async function api<T=any>(path:string,init:RequestInit={},token?:string|null):Promise<T>{
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),30000);
  try{
    const headers=new Headers(init.headers);if(token)headers.set('Authorization',`Bearer ${token}`);
    if(init.body&&!(init.body instanceof FormData)&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
    const response=await fetch(`${API_URL}${path}`,{...init,headers,signal:controller.signal});
    const raw=await response.text();let body:any={};try{body=raw?JSON.parse(raw):{}}catch{body={detail:raw||`Request failed (${response.status})`}}
    if(!response.ok)throw new Error(body.detail||body.message||`Request failed (${response.status})`);return body as T;
  }catch(error:any){if(error?.name==='AbortError')throw new Error('The server took too long to respond. Please try again.');throw error}finally{clearTimeout(timeout)}
}

export type AppContext={business:{id:string;name:string;role:string};store:{id:string;name:string};user:{id:string;name:string;email:string};subscription?:any};
export async function loadContext(token:string):Promise<AppContext>{
  const [user,businesses]=await Promise.all([api<any>('/me',{},token),api<any[]>('/businesses',{},token)]);const business=businesses[0];if(!business)throw new Error('No store is connected to this account.');
  const [stores,subscription]=await Promise.all([api<any[]>(`/businesses/${business.id}/stores`,{},token),api(`/businesses/${business.id}/subscription`,{},token)]);if(!stores[0])throw new Error('No store location was found.');
  return {user,business,store:stores[0],subscription};
}
