"use client";

import { useMemo, useState } from "react";

type Product = { id: string; name: string; unit: string; stock: number; reorder: number; price: number; cost: number; sold: number };
type Sale = { id: string; productId: string; product: string; qty: number; amount: number; payment: string };

const initialProducts: Product[] = [
  { id: "P-001", name: "Aashirvaad Atta 5 kg", unit: "bag", stock: 7, reorder: 12, price: 298, cost: 258, sold: 9 },
  { id: "P-002", name: "Fortune Sunlite Oil 1 L", unit: "pouch", stock: 4, reorder: 10, price: 132, cost: 118, sold: 12 },
  { id: "P-003", name: "Tata Salt 1 kg", unit: "packet", stock: 18, reorder: 10, price: 28, cost: 24, sold: 7 },
  { id: "P-004", name: "Parle-G 800 g", unit: "packet", stock: 0, reorder: 16, price: 85, cost: 72, sold: 14 },
  { id: "P-005", name: "Amul Taaza 1 L", unit: "carton", stock: 9, reorder: 8, price: 74, cost: 68, sold: 6 },
  { id: "P-006", name: "Surf Excel 1 kg", unit: "packet", stock: 5, reorder: 8, price: 154, cost: 137, sold: 4 },
];

const money = (value: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value);

export default function DailyOps() {
  const [products, setProducts] = useState(initialProducts);
  const [sales, setSales] = useState<Sale[]>([
    { id: "S-2401", productId: "P-002", product: "Fortune Sunlite Oil 1 L", qty: 3, amount: 396, payment: "UPI" },
    { id: "S-2402", productId: "P-004", product: "Parle-G 800 g", qty: 2, amount: 170, payment: "Cash" },
  ]);
  const [expenses, setExpenses] = useState(780);
  const [view, setView] = useState("Home");
  const [notice, setNotice] = useState("Today’s entry is 72% complete");
  const [productId, setProductId] = useState("P-001");
  const [qty, setQty] = useState(1);
  const [payment, setPayment] = useState("Cash");
  const [mobileNav, setMobileNav] = useState(false);

  const totals = useMemo(() => {
    const salesTotal = sales.reduce((sum, sale) => sum + sale.amount, 0);
    const cogs = sales.reduce((sum, sale) => sum + (products.find(p => p.id === sale.productId)?.cost ?? 0) * sale.qty, 0);
    const low = products.filter(p => p.stock > 0 && p.stock <= p.reorder).length;
    const out = products.filter(p => p.stock <= 0).length;
    return { salesTotal, profit: salesTotal - cogs, low, out, stockValue: products.reduce((sum, p) => sum + p.stock * p.cost, 0) };
  }, [sales, products]);

  function addSale(e: React.FormEvent) {
    e.preventDefault();
    const product = products.find(p => p.id === productId)!;
    if (qty <= 0 || qty > product.stock) { setNotice(`Only ${product.stock} ${product.unit}${product.stock === 1 ? "" : "s"} available`); return; }
    const sale = { id: `S-${Date.now().toString().slice(-5)}`, productId, product: product.name, qty, amount: qty * product.price, payment };
    setSales(current => [sale, ...current]);
    setProducts(current => current.map(p => p.id === productId ? { ...p, stock: p.stock - qty, sold: p.sold + qty } : p));
    setNotice(`${product.name}: sale saved and stock updated`);
    setQty(1);
  }

  const selected = products.find(p => p.id === productId)!;
  const nav = ["Home", "Daily entry", "Inventory", "Udhaar", "Purchases", "Reports"];

  return (
    <main className="app-shell">
      <aside className={mobileNav ? "sidebar open" : "sidebar"}>
        <div className="brand"><span className="brand-mark">द</span><div><strong>DailyOps</strong><small>GUPTA KIRANA STORE</small></div></div>
        <nav>{nav.map(item => <button key={item} className={view === item ? "active" : ""} onClick={() => { setView(item); setMobileNav(false); }}><span>{item === "Home" ? "⌂" : item === "Daily entry" ? "+" : item === "Inventory" ? "▦" : item === "Udhaar" ? "₹" : item === "Purchases" ? "⇩" : "▥"}</span>{item}</button>)}</nav>
        <div className="side-foot"><button onClick={() => setView("Settings")}>⚙ Settings</button><div className="user"><span>RG</span><div><b>Rajesh Gupta</b><small>Owner</small></div></div></div>
      </aside>

      <section className="workspace">
        <header><button className="menu" onClick={() => setMobileNav(!mobileNav)}>☰</button><div><h1>{view === "Home" ? "Namaste, Rajesh" : view}</h1><p>Tuesday, 21 July · Today’s business at a glance</p></div><div className="header-actions"><button className="icon-button" aria-label="Notifications">♢<i>4</i></button><button className="primary" onClick={() => setView("Daily entry")}>＋ Daily entry</button></div></header>

        {view === "Daily entry" ? (
          <div className="entry-page">
            <div className="section-title"><div><span className="eyebrow">FAST ENTRY</span><h2>Record today’s sale</h2><p>Stock updates automatically after you save.</p></div><button className="ghost" onClick={() => setView("Home")}>← Dashboard</button></div>
            <div className="entry-grid">
              <form className="panel sale-form" onSubmit={addSale}>
                <label>Product<select value={productId} onChange={e => setProductId(e.target.value)}>{products.map(p => <option value={p.id} key={p.id}>{p.name} · {p.stock} left</option>)}</select></label>
                <div className="field-row"><label>Quantity<input type="number" min="1" value={qty} onChange={e => setQty(Number(e.target.value))}/></label><label>Payment<select value={payment} onChange={e => setPayment(e.target.value)}><option>Cash</option><option>UPI</option><option>Card</option><option>Customer credit</option></select></label></div>
                <div className="bill-preview"><span>Sale amount</span><strong>{money(selected.price * qty)}</strong><small>{qty} × {money(selected.price)} · {selected.stock} {selected.unit}s in stock</small></div>
                <button className="primary wide" type="submit">Save sale & update stock</button>
              </form>
              <div className="panel"><div className="panel-head"><h3>Recent entries</h3><span>{sales.length} today</span></div>{sales.slice(0, 6).map(s => <div className="sale-row" key={s.id}><span className="product-icon">{s.product.slice(0,1)}</span><div><b>{s.product}</b><small>{s.qty} units · {s.payment}</small></div><strong>{money(s.amount)}</strong></div>)}</div>
            </div>
          </div>
        ) : (
          <>
            <div className="status-strip"><span className="pulse"></span><b>{notice}</b><div className="progress"><i style={{width:"72%"}}></i></div><button onClick={() => setView("Daily entry")}>Continue entry →</button></div>
            <section className="metrics">
              <article><div className="metric-top"><span className="metric-icon green">₹</span><em>↑ 12%</em></div><p>Today’s sales</p><strong>{money(totals.salesTotal)}</strong><small>Across {sales.length} entries</small></article>
              <article><div className="metric-top"><span className="metric-icon amber">↗</span><em>Estimate</em></div><p>Gross profit</p><strong>{money(totals.profit)}</strong><small>{totals.salesTotal ? Math.round(totals.profit / totals.salesTotal * 100) : 0}% margin</small></article>
              <article><div className="metric-top"><span className="metric-icon blue">▦</span><em>{money(totals.stockValue)}</em></div><p>Products need stock</p><strong>{totals.low + totals.out}</strong><small>{totals.out} out of stock</small></article>
              <article><div className="metric-top"><span className="metric-icon coral">₹</span><em>3 overdue</em></div><p>Customer udhaar</p><strong>{money(18450)}</strong><small>{money(4250)} due this week</small></article>
            </section>

            <section className="main-grid">
              <div className="panel action-panel"><div className="panel-head"><div><span className="eyebrow">NEEDS ATTENTION</span><h2>What to do next</h2></div><button>View all 8</button></div>
                <div className="action critical"><span className="action-symbol">!</span><div><b>Parle-G is out of stock</b><p>You sell about 14 packets a day. Reorder now to avoid missed sales.</p><small>Estimated missed sales: {money(1190)}/day</small></div><button onClick={() => setView("Purchases")}>Add to purchase</button></div>
                <div className="action warning"><span className="action-symbol">↓</span><div><b>Fortune Oil may run out tomorrow</b><p>Only 4 pouches left. Your supplier usually takes 2 days.</p><small>Suggested order: 24 pouches</small></div><button onClick={() => setView("Purchases")}>Review</button></div>
                <div className="action credit"><span className="action-symbol">₹</span><div><b>3 customer payments are overdue</b><p>Oldest pending: Sunita Sharma · 18 days overdue</p><small>Total overdue: {money(6850)}</small></div><button onClick={() => setView("Udhaar")}>View udhaar</button></div>
              </div>

              <div className="panel quick"><div className="panel-head"><div><span className="eyebrow">QUICK ACTIONS</span><h2>Enter today’s data</h2></div></div><div className="quick-grid">
                {[['＋','Sales','Record what sold'],['⇩','Purchase','Stock received'],['₹','Payment','Udhaar collected'],['−','Expense','Money spent'],['✓','Stock count','Check inventory'],['⇧','Upload Excel','Bulk entry']].map(([icon,title,sub]) => <button key={title} onClick={() => title === "Sales" ? setView("Daily entry") : setNotice(`${title} workflow is ready in the full navigation`)}><span>{icon}</span><b>{title}</b><small>{sub}</small></button>)}
              </div></div>
            </section>

            <section className="bottom-grid">
              <div className="panel"><div className="panel-head"><div><span className="eyebrow">LAST 7 DAYS</span><h2>Sales trend</h2></div><span className="legend"><i></i>Net sales</span></div><div className="chart">{[48,66,57,78,70,92,74].map((h,i)=><div key={i}><span style={{height:`${h}%`}}></span><small>{['Wed','Thu','Fri','Sat','Sun','Mon','Today'][i]}</small></div>)}</div></div>
              <div className="panel stock-list"><div className="panel-head"><div><span className="eyebrow">PURCHASE PLAN</span><h2>Suggested order</h2></div><button onClick={() => setView("Purchases")}>Open list</button></div>{products.filter(p => p.stock <= p.reorder).slice(0,4).map(p => <div key={p.id}><span className="product-icon">{p.name[0]}</span><p><b>{p.name}</b><small>{p.stock === 0 ? "Out of stock" : `${p.stock} ${p.unit}s left`}</small></p><strong>{Math.max(0, p.reorder * 2 - p.stock)} <small>{p.unit}s</small></strong></div>)}</div>
            </section>
            <div className="expense-note">Today’s expenses: <b>{money(expenses)}</b><button onClick={() => {setExpenses(expenses + 250); setNotice("Expense saved · estimated profit refreshed")}}>+ Add ₹250 demo expense</button></div>
          </>
        )}
      </section>
    </main>
  );
}
