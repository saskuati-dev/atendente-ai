-- Valores monetários em centavos (INTEGER), para evitar erros de ponto flutuante.

CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
) STRICT;

CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    category_id    INTEGER NOT NULL REFERENCES categories (category_id),
    description    TEXT NOT NULL,
    price_cents    INTEGER NOT NULL CHECK (price_cents > 0),
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status         TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    specs          TEXT NOT NULL CHECK (json_valid(specs)),
    created_at     TEXT NOT NULL CHECK (date(created_at) IS NOT NULL),
    search_text    TEXT NOT NULL
) STRICT;

CREATE INDEX idx_products_category ON products (category_id);

-- O e-mail não é único: os clientes 11 e 43 compartilham o mesmo endereço.
CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    phone        TEXT NOT NULL,
    phone_digits TEXT NOT NULL UNIQUE,
    email        TEXT NOT NULL,
    city         TEXT NOT NULL
) STRICT;

CREATE INDEX idx_customers_email ON customers (email);

CREATE TABLE orders (
    order_id           INTEGER PRIMARY KEY,
    customer_id        INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date         TEXT NOT NULL CHECK (date(order_date) IS NOT NULL),
    status             TEXT NOT NULL
        CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    total_cents        INTEGER NOT NULL CHECK (total_cents > 0),
    payment_method     TEXT NOT NULL
        CHECK (payment_method IN ('pix', 'debit', 'boleto') OR payment_method GLOB 'credit_*x'),
    tracking_code      TEXT,
    estimated_delivery TEXT CHECK (estimated_delivery IS NULL OR date(estimated_delivery) IS NOT NULL),
    notes              TEXT
) STRICT;

CREATE INDEX idx_orders_customer ON orders (customer_id);

CREATE TABLE order_items (
    order_id   INTEGER NOT NULL REFERENCES orders (order_id),
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, product_id)
) STRICT;

CREATE TABLE promotions (
    promotion_id     INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES products (product_id),
    discount_percent INTEGER NOT NULL CHECK (discount_percent BETWEEN 1 AND 100),
    description      TEXT NOT NULL,
    is_active        INTEGER NOT NULL CHECK (is_active IN (0, 1))
) STRICT;

-- Promoções não são cumulativas (seção 6.2): no máximo uma ativa por produto.
CREATE UNIQUE INDEX idx_promotions_one_active ON promotions (product_id) WHERE is_active = 1;
