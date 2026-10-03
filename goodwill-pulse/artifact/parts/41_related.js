/* ---------------- Related dashboards: which KPI tiles fit a question (contract 4) ---------------- */
const RK_PILLAR = {total_revenue: "growth", revenue_growth_yoy: "growth", budget_attainment: "growth", ecom_share_of_retail: "growth",
  net_margin: "profitability", gross_margin: "profitability", profit_per_labor_hour: "profitability", top_categories_by_margin: "profitability",
  revenue_per_labor_hour: "productivity", listings_created: "productivity", listings_per_day: "productivity", listings_per_employee: "productivity", sales_per_employee: "productivity",
  avg_time_to_list: "productivity", items_identified: "productivity", items_sent: "productivity",
  sell_through: "inventory", days_donation_to_listing: "inventory", unlisted_backlog: "inventory", unsold_pct: "inventory", days_to_sell: "inventory", relisted_pct: "inventory",
  avg_selling_price: "inventory", median_sale_price: "inventory", top_categories_by_revenue: "inventory",
  repeat_buyer_rate: "engagement", buyers: "engagement", new_buyers: "engagement", refund_rate: "engagement", customer_satisfaction: "engagement", net_promoter_score: "engagement", marketplace_conversion: "engagement"};
const RK_METRIC = {
  items_listed: [["listings_created", "listings_per_employee", "unlisted_backlog", "days_donation_to_listing"], "listing volume"],
  items_sent: [["items_sent", "listings_created", "unlisted_backlog", "days_donation_to_listing"], "items sent to e-commerce"],
  items_identified: [["items_identified", "items_sent", "listings_created", "unlisted_backlog"], "items identified"],
  item_sales: [["total_revenue", "revenue_growth_yoy", "budget_attainment", "avg_selling_price"], "sales"],
  store_revenue: [["total_revenue", "revenue_growth_yoy", "budget_attainment", "avg_selling_price"], "sales credited to stores"],
  units_sold: [["total_revenue", "avg_selling_price", "sell_through", "revenue_growth_yoy"], "units sold"],
  orders: [["total_revenue", "revenue_growth_yoy", "budget_attainment", "avg_selling_price"], "order volume"],
  avg_order: [["avg_selling_price", "median_sale_price", "total_revenue"], "order value"],
  fees: [["net_margin", "gross_margin", "refund_rate"], "marketplace fees and margin"],
  refunds: [["refund_rate", "net_margin", "gross_margin"], "refunds and margin"],
  shipping: [["net_margin", "gross_margin"], "shipping and margin"],
};
function relatedKpis(spec, claudeHint) {
  const sp = spec || {}, hint = claudeHint || {}, q = String(hint.q || "").toLowerCase();
  const claude = (Array.isArray(hint.related_kpis) ? hint.related_kpis : Array.isArray(hint) ? hint : []).filter(id => RK_PILLAR[id]);
  const row = RK_METRIC[sp.metric] || RK_METRIC.item_sales;
  let ids = [...claude], topic = row[1];
  const add = list => { for (const id of list) if (!ids.includes(id)) ids.push(id); };
  if (/buyer|customer|repeat|loyal/.test(q)) { add(["repeat_buyer_rate", "buyers", "new_buyers"]); topic = "buyers"; if (!claude.length) ids = ids.filter(id => RK_PILLAR[id] === "engagement").concat(ids.filter(id => RK_PILLAR[id] !== "engagement")); }
  if (sp.by === "category") add(["top_categories_by_revenue", "top_categories_by_margin"]);
  add(row[0]);
  ids = ids.slice(0, 4);
  const pillar = RK_PILLAR[ids[0]] || "growth";
  const by = sp.by === "store" ? ", by store" : sp.by === "category" ? ", by category" : "";
  const reason = claude.length ? `Shown because Claude linked the question to these measures (${row[1]}${by}).` : `Shown because the question is about ${topic}${by}.`;
  return {pillar, ids, reason, stores: sp.by === "store" || !!(sp.filters && sp.filters.store), categories: sp.by === "category" || !!(sp.filters && sp.filters.category)};
}
