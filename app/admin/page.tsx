"use client";
import {useEffect,useState} from "react";

type Row=Record<string,any>;
const API="http://localhost:8000/api";
const number=(value:unknown)=>Number(value||0).toLocaleString("en-IN");
const date=(value:string|null)=>value?new Date(value).toLocaleDateString("en-IN",{day:"2-digit",month:"short",year:"numeric"}):"—";

export default function AdminConsole(){
  const [token,setToken]=useState(""),[username,setUsername]=useState("id"),[password,setPassword]=useState("root");
  const [data,setData]=useState<Row|null>(null),[status,setStatus]=useState(""),[tab,setTab]=useState<"users"|"subscriptions">("users"),[busy,setBusy]=useState("");
  async function call(path:string,options:RequestInit={}){
    const response=await fetch(`${API}${path}`,options);
    if(!response.ok){let body:any={};try{body=await response.json()}catch{}throw new Error(body.detail||"Request failed")}
    return response.json();
  }
  const adminOptions=(method="GET"):RequestInit=>({method,headers:{Authorization:`Bearer ${token}`}});
  async function load(value=token){const result=await call("/admin/overview",{headers:{Authorization:`Bearer ${value}`}});setData(result)}
  useEffect(()=>{const saved=sessionStorage.getItem("kirana_admin_token");if(saved){setToken(saved);load(saved).catch(()=>sessionStorage.removeItem("kirana_admin_token"))}},[]);
  async function login(event:React.FormEvent){
    event.preventDefault();setStatus("Opening master console…");
    try{
      const result=await call("/admin/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username,password})});
      setToken(result.access_token);sessionStorage.setItem("kirana_admin_token",result.access_token);await load(result.access_token);setStatus("");
    }catch(error:any){setStatus(error.message)}
  }
  async function membership(user:Row,action:"grant"|"end"){
    setBusy(`${action}-${user.id}`);setStatus("");
    try{
      const result=await call(`/admin/users/${user.id}/subscription`,adminOptions(action==="grant"?"POST":"DELETE"));
      setStatus(result.message);await load();
    }catch(error:any){setStatus(error.message)}finally{setBusy("")}
  }
  async function removeUser(user:Row){
    if(!window.confirm(`Permanently delete ${user.name} and all data in their owned store? This cannot be undone.`))return;
    setBusy(`delete-${user.id}`);setStatus("");
    try{
      const result=await call(`/admin/users/${user.id}`,adminOptions("DELETE"));setStatus(result.message);await load();
    }catch(error:any){setStatus(error.message)}finally{setBusy("")}
  }
  if(!token||!data)return <main className="admin-auth"><form onSubmit={login}><span className="admin-badge">MASTER CONSOLE</span><h1>KiranaSaathi Admin</h1><p>Monitor accounts, subscriptions, data volume and AI usage.</p><label>Admin ID<input value={username} onChange={event=>setUsername(event.target.value)} required/></label><label>Password<input type="password" value={password} onChange={event=>setPassword(event.target.value)} required/></label><button>Open console</button><a href="/">Back to customer login</a>{status&&<div className="admin-error">{status}</div>}</form></main>;
  const summary=data.summary||{};
  return <main className="admin-console">
    <header><div><span className="admin-badge">MASTER CONSOLE</span><h1>Platform overview</h1><p>Customer accounts, memberships, stored data and Gemini consumption.</p></div><div className="admin-actions"><button onClick={()=>load().catch(error=>setStatus(error.message))}>Refresh</button><button onClick={()=>{sessionStorage.removeItem("kirana_admin_token");setToken("");setData(null)}}>Sign out</button></div></header>
    <section className="admin-kpis"><article><small>Users</small><strong>{number(summary.users)}</strong></article><article><small>Businesses</small><strong>{number(summary.businesses)}</strong></article><article><small>Data records</small><strong>{number(summary.data_records)}</strong></article><article><small>AI requests</small><strong>{number(summary.ai_requests)}</strong></article><article><small>AI tokens</small><strong>{number(summary.ai_tokens)}</strong></article></section>
    <nav className="admin-tabs"><button className={tab==="users"?"active":""} onClick={()=>setTab("users")}>Users</button><button className={tab==="subscriptions"?"active":""} onClick={()=>setTab("subscriptions")}>Subscriptions</button></nav>
    {tab==="users"?<section className="admin-panel">
      <div className="admin-panel-head"><div><h2>Customer accounts</h2><p>Deleting an account permanently removes its owned store data.</p></div><b>{data.users.length} accounts</b></div>
      <div className="admin-user-table"><div className="admin-user-row head"><b>User</b><b>Store</b><b>Joined on</b><b>Data</b><b>AI requests</b><b>Actions</b></div>{data.users.map((user:Row)=><div className="admin-user-row" key={user.id}><div><b>{user.name}</b><small>{user.email||"Legacy account — email not set"}</small></div><div>{user.businesses.length?user.businesses.map((business:Row)=><span key={business.id}>{business.name}</span>):<span>No store</span>}</div><span className="joined-date"><b>{date(user.created_at)}</b><small>{new Date(user.created_at).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"})}</small></span><strong>{number(user.data_records)}</strong><strong>{number(user.ai_requests)}</strong><button className="admin-delete" disabled={busy===`delete-${user.id}`} onClick={()=>removeUser(user)}>{busy===`delete-${user.id}`?"Deleting…":"Delete user"}</button></div>)}</div>
    </section>:<section className="admin-panel">
      <div className="admin-plan"><div><span className="admin-badge">ACTIVE OFFER</span><h2>Starter plan</h2><p>Includes every KiranaSaathi feature currently available.</p></div><strong>₹599 <small>/ month</small></strong></div>
      <div className="admin-panel-head"><div><h2>User memberships</h2><p>Activate a fresh 30-day membership or end access immediately.</p></div><b>{data.users.length} accounts</b></div>
      <div className="admin-sub-table"><div className="admin-sub-row head"><b>User</b><b>Store</b><b>Plan</b><b>Status</b><b>Period</b><b>Action</b></div>{data.users.map((user:Row)=>{
        const business=user.businesses[0],subscription=business?.subscription,isActive=Boolean(subscription?.access_active);
        const statusLabel=subscription?.status==="trial"?"Free trial":subscription?.status==="active"?"Active":subscription?.status==="expired"?"Expired":subscription?.status==="ended"?"Revoked":"Inactive";
        return <div className="admin-sub-row" key={user.id}><div><b>{user.name}</b><small>{user.email}</small></div><span>{business?.name||"No store"}</span><div><b>Starter</b><small>₹599/month</small></div><span><i className={`membership-status ${isActive?"active":"inactive"}`}>{statusLabel}</i>{isActive&&<small>{subscription.days_remaining} day{subscription.days_remaining===1?"":"s"} left</small>}</span><div><small>Start: {date(subscription?.started_at)}</small><small>End: {date(subscription?.ends_at)}</small></div><div className="membership-actions"><button disabled={!business||busy===`grant-${user.id}`} onClick={()=>membership(user,"grant")}>{busy===`grant-${user.id}`?"Activating…":isActive?"Add 30 days":"Give membership"}</button>{isActive&&<button className="end" disabled={busy===`end-${user.id}`} onClick={()=>membership(user,"end")}>{busy===`end-${user.id}`?"Revoking…":"Revoke"}</button>}</div></div>
      })}</div>
    </section>}
    <section className="admin-panel"><div className="admin-panel-head"><div><h2>Daily platform usage</h2><p>Business actions and AI usage by day</p></div></div><div className="admin-daily-table"><div className="admin-daily-row head"><b>Date</b><b>Data actions</b><b>AI requests</b><b>AI tokens</b></div>{data.daily_usage.map((row:Row)=><div className="admin-daily-row" key={row.date}><b>{new Date(`${row.date}T12:00:00`).toLocaleDateString("en-IN")}</b><span>{number(row.data_actions)}</span><span>{number(row.ai_requests)}</span><strong>{number(row.ai_tokens)}</strong></div>)}</div></section>
    {status&&<div className="admin-toast" onClick={()=>setStatus("")}>{status}</div>}
  </main>
}
