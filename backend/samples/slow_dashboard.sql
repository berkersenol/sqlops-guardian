-- Sample: Slow dashboard query with multiple issues
SELECT * FROM customers c
LEFT JOIN orders o ON c.id = o.customer_id
LEFT JOIN order_items oi ON o.id = oi.order_id
WHERE EXTRACT(YEAR FROM o.created_at) = 2025
GROUP BY c.id
ORDER BY SUM(o.total) DESC;
