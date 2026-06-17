CREATE TABLE jokes
(
    id         BIGSERIAL    NOT NULL,
    setup      VARCHAR(500) NOT NULL,
    punchline  VARCHAR(500) NOT NULL,
    CONSTRAINT pk_jokes PRIMARY KEY (id)
);
