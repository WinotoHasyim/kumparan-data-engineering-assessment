SET TIME ZONE 'UTC';

INSERT INTO public.articles (
    id,
    title,
    content,
    published_at,
    author_id,
    created_at,
    updated_at,
    deleted_at
)
VALUES
    (
        1000001,
        'Artikel arsip dari 2016',
        'Artikel deterministik untuk membuktikan initial historical backfill.',
        '2016-02-15 08:00:00+00',
        101,
        '2016-02-15 07:30:00+00',
        '2016-02-15 08:00:00+00',
        NULL
    ),
    (
        1000002,
        'Artikel terbaru untuk incremental load',
        'Artikel ini dapat diubah untuk mendemonstrasikan proses upsert.',
        '2026-11-12 10:15:00+00',
        102,
        '2026-11-12 10:00:00+00',
        '2026-11-12 10:15:00+00',
        NULL
    ),
    (
        1000003,
        'Draft yang belum diterbitkan',
        'Nilai published_at sengaja null untuk menguji klasifikasi draft.',
        NULL,
        103,
        '2026-11-12 11:00:00+00',
        '2026-11-12 11:00:00+00',
        NULL
    ),
    (
        1000004,
        'Unicode: Banjir melanda Jakarta — warga bergotong royong',
        'Konten berbahasa Indonesia dengan karakter Unicode: “aman”, café, 日本語, dan emoji 🌧️.',
        '2026-11-12 11:30:00+00',
        104,
        '2026-11-12 11:10:00+00',
        '2026-11-12 11:30:00+00',
        NULL
    ),
    (
        1000005,
        'Timestamp tie A',
        'Memastikan pagination tidak kehilangan baris dengan updated_at yang sama.',
        '2026-11-12 12:30:00+00',
        105,
        '2026-11-12 12:00:00+00',
        '2026-11-12 13:00:00+00',
        NULL
    ),
    (
        1000006,
        'Timestamp tie B',
        'Pasangan timestamp tie yang harus dibedakan menggunakan article id.',
        '2026-11-12 12:35:00+00',
        106,
        '2026-11-12 12:05:00+00',
        '2026-11-12 13:00:00+00',
        NULL
    ),
    (
        1000007,
        'Artikel soft deleted',
        'Baris tetap berada di source tetapi mempunyai deleted_at.',
        '2026-11-12 13:15:00+00',
        107,
        '2026-11-12 13:00:00+00',
        '2026-11-12 14:00:00+00',
        '2026-11-12 14:00:00+00'
    ),
    (
        1000008,
        'Kandidat demonstrasi hard delete',
        'Gunakan mutation CLI untuk menghapus baris ini secara fisik.',
        '2026-11-12 14:15:00+00',
        108,
        '2026-11-12 14:00:00+00',
        '2026-11-12 14:15:00+00',
        NULL
    ),
    (
        1000009,
        'Artikel dengan konten panjang',
        repeat('Paragraf konten panjang untuk pengujian ukuran data dan serialisasi. ', 500),
        '2026-11-12 15:10:00+00',
        109,
        '2026-11-12 15:00:00+00',
        '2026-11-12 15:10:00+00',
        NULL
    ),
    (
        1000010,
        'Exact hourly boundary',
        '<p>Updated tepat pada batas jam untuk menguji interval [lower, upper).</p>',
        '2026-11-13 00:00:00+00',
        110,
        '2026-11-12 23:50:00+00',
        '2026-11-13 00:00:00+00',
        NULL
    )
ON CONFLICT (id) DO NOTHING;

-- Add one predictable historical anchor for every intermediate year.
INSERT INTO public.articles (
    id,
    title,
    content,
    published_at,
    author_id,
    created_at,
    updated_at,
    deleted_at
)
SELECT
    1100000 + seed_year,
    format('Artikel historis %s', seed_year),
    format('Data sintetis untuk memverifikasi backfill tahun %s.', seed_year),
    make_timestamptz(seed_year, 6, 15, 8, 0, 0, 'UTC'),
    200 + seed_year,
    make_timestamptz(seed_year, 6, 15, 7, 30, 0, 'UTC'),
    make_timestamptz(seed_year, 6, 15, 8, 0, 0, 'UTC'),
    NULL
FROM generate_series(2017, 2025) AS years(seed_year)
ON CONFLICT (id) DO NOTHING;

-- Advances the identity sequence past all explicit seed IDs.
SELECT setval(
    pg_get_serial_sequence('public.articles', 'id'),
    (SELECT max(id) FROM public.articles),
    true
);
