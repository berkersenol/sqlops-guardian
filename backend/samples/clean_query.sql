-- Sample: Well-written query
SELECT c.id, c.name, c.email, 
       SUM(o.total) as total_revenue,
       COUNT(o.id) as order_count
FROM customers c
JOIN orders o ON c.id = o.customer_id
WHERE o.created_at >= '2025-01-01' AND o.created_at < '2026-01-01'
GROUP BY c.id, c.name, c.email
ORDER BY total_revenue DESC
LIMIT 50;
