let SRC = [],
    TGT = [],
    SAMPLE = [],
    RULES = {},
    MAX = 50,
    KEY = '';

const S = {
    versions: [],
    sel: 0,
    runs: [],
    hist: [],
    dry: {},
    recon: {},
    preview: {},
    agent: null,
    tab: document.body.dataset.page || 'agent',
    open: {},
    fail: false,
    loaded: false
};

const FILE = {
    agent: 'index.html',
    plan: 'plan.html',
    dry: 'dry.html',
    exec: 'execute.html',
    hist: 'history.html',
    data: 'data.html'
};

const save = () => { };

const curV = () => S.versions[S.sel];

const hash = (s) => {
    let h = 5381;

    for (const c of s) {
        h = (h * 33 ^ c.charCodeAt(0)) >>> 0;
    }

    return h.toString(16);
};

function toast(m) {
    let t = document.getElementById('toast');

    if (!t) {
        t = document.createElement('div');
        t.id = 'toast';
        document.body.appendChild(t);
    }

    t.textContent = m;
    t.className = 's';

    clearTimeout(t._h);

    t._h = setTimeout(() => {
        t.className = '';
    }, 3000);
}

async function api(u, b, m) {
    try {
        const r = await fetch(u, {
            method: m || (b ? 'POST' : 'GET'),
            headers: {
                'Content-Type': 'application/json'
            },
            body: b ? JSON.stringify(b) : undefined
        });

        const j = await r.json();

        if (!r.ok) {
            throw new Error(j.error || r.status);
        }

        return j;
    } catch (e) {
        toast('✕ ' + e.message);
        throw e;
    }
}

async function load() {
    const j = await api('/api/state');
    const { meta, ...rest } = j;

    SRC = meta.src;
    TGT = meta.tgt;
    SAMPLE = meta.sample;
    MAX = meta.max;

    RULES = Object.fromEntries(
        Object.entries(meta.rules).map(([k, d]) => [
            k,
            { d }
        ])
    );

    Object.assign(S, rest, {
        loaded: true,
        llm: meta.llm
    });

    if (S.sel >= S.versions.length) {
        S.sel = Math.max(0, S.versions.length - 1);
    }
}

async function busy(label, fn, ok) {
    toast('⏳ ' + label);

    try {
        const r = await fn();

        await load();
        render();

        toast('✓ ' + (ok || label));

        return r;
    } catch (e) {
        await load().catch(() => { });
        render();
    }
}

const runAgent = () =>
    busy(
        'Agent running (tool calls + validation)…',
        async () => {
            const r = await api('/api/agent', {});
            S.sel = S.versions.length;

            return r;
        },
        'Agent plan drafted — review before approving'
    ).then(() => {
        S.sel = S.versions.length - 1;
        render();
    });

const runDry = () => {
    const b = $('drb');

    if (b) {
        b.disabled = true;
        b.innerHTML = '<span class="sp"></span> Running…';
    }

    return busy(
        'Dry run',
        () => api(`/api/versions/${curV().v}/dry`, {})
    ).then(() => {
        S.f = 'all';
        S.ef = null;
        render();
    });
};

const act = (a) =>
    busy(
        a,
        () =>
            api(`/api/versions/${curV().v}/${a}`, {
                simulate_failure: S.fail
            }).finally(() => {
                S.fail = false;
            })
    );

const approve = () => act('approve');

const newVer = () =>
    busy(
        'New version',
        () => api(`/api/versions/${curV().v}/clone`, {})
    ).then(() => {
        S.sel = S.versions.length - 1;
        render();
    });

const resetAll = () =>
    busy(
        'Reset',
        () => api('/api/reset', {})
    );

function edit(t, k, val) {
    const v = curV();

    v.map[t] = {
        ...(v.map[t] || {
            src: null,
            rule: null
        }),
        [k]: val || null
    };

    api(
        `/api/versions/${v.v}`,
        {
            map: v.map
        },
        'PUT'
    )
        .then(load)
        .then(render)
        .catch(() =>
            load().then(render)
        );
}

const $ = (s) => document.getElementById(s);

const esc = (s) =>
    String(s ?? '').replace(
        /[&<>]/g,
        (c) =>
            ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;'
            })[c]
    );

// ---- UI ----

const TABS = [
    ['agent', '1 · Agent'],
    ['plan', '2 · Plan'],
    ['dry', '3 · Dry run'],
    ['exec', '4 · Execute'],
    ['hist', '5 · History'],
    ['data', 'Data']
];

const kp = (a) =>
    `<div class="k">${a
        .map(
            ([n, l, c]) =>
                `<div>
                    <b class="${c || ''}" ${typeof n == 'number'
                    ? `data-n="${n}"`
                    : ''
                }>${n}</b>
                    <span>${l}</span>
                </div>`
        )
        .join('')}</div>`;

function render() {
    const cv = curV();

    const dn = {
        agent: !!S.agent,
        plan: S.versions.some(
            (x) => x.status == 'approved'
        ),
        dry: !!(cv && S.dry[cv.v]),
        exec: S.runs.some(
            (r) => r.status == 'complete'
        ),
        hist: S.hist.length > 0
    };

    $('nav').innerHTML =
        TABS.map(
            ([k, l], i) =>
                `<a class="${S.tab == k ? 'on' : ''} ${dn[k] ? 'dn' : ''
                }" href="${FILE[k]}">
                    <i>${dn[k] ? '✓' : i + 1}</i>
                    ${l}
                </a>`
        ).join('') +
        `<button
            onclick="if(confirm('Reset everything?')){resetAll()}"
            style="margin-left:auto"
        >
            ↺ Reset
        </button>`;

    $('v').innerHTML = VIEWS[S.tab]();

    save();

    document.querySelectorAll('[data-n]').forEach((e) => {
        const n = +e.dataset.n;
        const t0 = performance.now();

        (function f(t) {
            const p = Math.min(
                1,
                (t - t0) / 500
            );

            e.textContent = Math.round(
                n * (1 - Math.pow(1 - p, 3))
            );

            if (p < 1) {
                requestAnimationFrame(f);
            }
        })(t0);
    });
}

const VIEWS = {
    data: () => `
        <div class="c">
            <h2>Source schema</h2>
            <div class="w">
                <table>
                    <tr>
                        <th>Field</th>
                        <th>Type</th>
                    </tr>

                    ${SRC.map(
        (x) =>
            `<tr>
                                <td>${x[0]}</td>
                                <td>${x[1]}</td>
                            </tr>`
    ).join('')}
                </table>
            </div>
        </div>

        <div class="c">
            <h2>Target schema</h2>
            <div class="w">
                <table>
                    <tr>
                        <th>Field</th>
                        <th>Type</th>
                        <th>Required</th>
                    </tr>

                    ${TGT.map(
        (x) =>
            `<tr>
                                <td>${x[0]}</td>
                                <td>${x[1]}</td>
                                <td>${x[2] ? 'yes' : 'no'}</td>
                            </tr>`
    ).join('')}
                </table>
            </div>
        </div>

        <div class="c">
            <h2>Supported transformation rules (no arbitrary code)</h2>

            <div class="w">
                <table>
                    ${Object.entries(RULES)
            .map(
                ([k, r]) =>
                    `<tr>
                                    <td><code>${k}</code></td>
                                    <td>${r.d}</td>
                                </tr>`
            )
            .join('')}
                </table>
            </div>
        </div>

        <div class="c">
            <h2>
                Sample source records
                (${SAMPLE.length}/${MAX})
            </h2>

            <div class="w">
                <table>
                    <tr>
                        ${SRC.map(
                (x) =>
                    `<th>${x[0]}</th>`
            ).join('')}
                    </tr>

                    ${SAMPLE.map(
                (r) =>
                    `<tr>
                                ${SRC.map(
                        (x) =>
                            `<td>${esc(
                                r[x[0]]
                            )}</td>`
                    ).join('')}
                            </tr>`
            ).join('')}
                </table>
            </div>
        </div>
    `,

    agent: () => {
        if (!S.agent) {
            return `
                <div class="c">
                    <h2>Migration agent</h2>

                    <p class="s">
                        The agent may only call three read-only tools:
                        <code>inspect_schema</code>,
                        <code>sample_values</code>,
                        <code>validate_rule</code>.
                        It cannot execute or approve anything.
                    </p>

                    <button class="p" onclick="runAgent()">
                        Run agent
                    </button>
                </div>
            `;
        }

        const a = S.agent;

        return `
            <div class="c">
                <h2>Tool calls</h2>

                <div class="w">
                    <table>
                        ${a.calls
                .map(
                    (c) =>
                        `<tr>
                                        <td>
                                            <code>${esc(c[0])}</code>
                                        </td>
                                        <td class="mu">
                                            ${c[1]}
                                        </td>
                                    </tr>`
                )
                .join('')}
                    </table>
                </div>
            </div>

            <div class="c">
                <h2>Risks</h2>

                ${a.risks
                .map(
                    (r) =>
                        `<p>
                                <span class="b ${r[0] == 'high'
                            ? 'er'
                            : r[0] == 'medium'
                                ? 'wa'
                                : 'mu'
                        }">
                                    ${r[0]}
                                </span>
                                ${esc(r[1])}
                            </p>`
                )
                .join('')}
            </div>

            <div class="c">
                <h2>Clarification questions</h2>

                <ol>
                    ${a.qs
                .map(
                    (q) =>
                        `<li>${esc(q)}</li>`
                )
                .join('')}
                </ol>

                <p class="mu">
                    Answer by editing the mapping in the Plan tab
                    and saving a new version.
                </p>
            </div>

            <button
                onclick="location.href='plan.html'"
                class="p"
            >
                Review proposed plan →
            </button>
        `;
    },

    plan: () => {
        if (!S.versions.length) {
            return '<div class="c">Run the agent first.</div>';
        }

        const v = curV();
        const ed = v.status == 'draft';
        const pv = S.versions[S.sel - 1];

        const rows = TGT.map(([t, ty, rq]) => {
            const m = v.map[t] || {};
            let pr;

            const r = (S.preview[v.v] || {})[t];

            if (m.src && m.rule && r) {
                const pct = Math.round(
                    (r.ok / r.total) * 100
                );

                pr = `
                    <div class="bar">
                        <i
                            style="width:${pct}%;background:var(--${pct == 100
                        ? 'ok'
                        : pct > 85
                            ? 'wa'
                            : 'er'
                    })"
                        ></i>
                    </div>

                    <small>
                        ${r.ok}/${r.total} pass ·
                        ${esc(r.example)}
                    </small>
                `;
            } else {
                pr = `
                    <small class="${rq ? 'er' : ''}">
                        ${rq
                        ? '⚠ Required field unmapped'
                        : 'Will be null'
                    }
                    </small>
                `;
            }

            const ch =
                pv &&
                JSON.stringify(pv.map[t] || {}) !==
                JSON.stringify(m);

            return `
                <div class="mrow">
                    <select
                        ${ed ? '' : 'disabled'}
                        onchange="edit('${t}','src',this.value);render()"
                    >
                        <option value="">
                            — no source —
                        </option>

                        ${SRC.map(
                (x) =>
                    `<option ${m.src == x[0]
                        ? 'selected'
                        : ''
                    }>
                                    ${x[0]}
                                </option>`
            ).join('')}
                    </select>

                    <select
                        ${ed ? '' : 'disabled'}
                        title="${m.rule
                    ? esc(RULES[m.rule].d)
                    : ''
                }"
                        onchange="edit('${t}','rule',this.value);render()"
                    >
                        <option value="">
                            — rule —
                        </option>

                        ${Object.keys(RULES)
                    .map(
                        (r) =>
                            `<option ${m.rule == r
                                ? 'selected'
                                : ''
                            }>
                                        ${r}
                                    </option>`
                    )
                    .join('')}
                    </select>

                    <div class="tf">
                        <b>→ ${t}</b>
                        ${rq ? '<em>req</em>' : ''}
                        ${ch
                    ? '<span class="b wa">changed</span>'
                    : ''
                }
                        <small>${ty}</small>
                    </div>

                    <div>${pr}</div>
                </div>
            `;
        }).join('');

        return `
            <div class="c">
                <h2>Plan versions</h2>

                <div class="vt">
                    ${S.versions
                .map(
                    (x, i) =>
                        `<button
                                    class="${i == S.sel ? 'on' : ''}"
                                    onclick="S.sel=${i};render()"
                                >
                                    v${x.v}
                                    ${x.status == 'approved'
                            ? '🔒'
                            : '✎'
                        }
                                </button>`
                )
                .join('')}
                </div>

                <small>
                    v${v.v} ·
                    ${esc(v.note)} ·
                    hash <code>${v.hash}</code> ·
                    ${v.at}
                    ${ed ? '' : ' · locked (approved)'}
                </small>
            </div>

            <div class="c">
                <h2>
                    Field mapping
                    <small>
                        (live pass-rate on sample;
                        hover rule for description)
                    </small>
                </h2>

                ${rows}

                <div
                    class="row"
                    style="margin-top:14px"
                >
                    ${ed
                ? `<button
                                class="p"
                                onclick="approve()"
                            >
                                ✔ Approve v${v.v}
                            </button>`
                : '<span class="b ok">Approved</span>'
            }

                    <button onclick="newVer()">
                        ⎘ Save as new version
                        (v${S.versions.length + 1})
                    </button>
                </div>
            </div>
        `;
    },

    dry: () => {
        const v = curV();

        if (!v) {
            return '<div class="c">Run the agent first.</div>';
        }

        const d = S.dry[v.v];

        const btn = `
            <div class="c hero2">
                <div>
                    <h2>Deterministic dry run · v${v.v}</h2>
                    <small>
                        Same plan + same sample ⇒ identical output
                    </small>
                </div>

                <button
                    id="drb"
                    class="p"
                    onclick="runDry()"
                >
                    ▶ ${d ? 'Re-run' : 'Run'} dry run
                </button>
            </div>
        `;

        if (!d) {
            return btn;
        }

        const T = d.src;
        const a = d.acc.length;
        const r = d.rej.length;
        const f = S.f || 'all';
        const by = {};

        d.rej.forEach((x) =>
            x.errs.forEach(
                (e) =>
                (by[e.field] =
                    (by[e.field] || 0) + 1)
            )
        );

        const chip = (l, fv, ef) =>
            `<button
                class="${(S.f || 'all') == fv &&
                (S.ef || null) == (ef || null)
                ? 'on'
                : ''
            }"
                onclick="S.f='${fv}';S.ef=${ef ? `'${ef}'` : 'null'
            };render()"
            >
                ${l}
            </button>`;

        const rej = d.rej
            .filter(
                (x) =>
                    !S.ef ||
                    x.errs.some(
                        (e) => e.field == S.ef
                    )
            )
            .map((x) => {
                const o = S.open[x.row];

                return `
                    <div
                        class="qc"
                        onclick="S.open[${x.row}]=!S.open[${x.row}];render()"
                    >
                        <div class="qh">
                            <b>Row ${x.row}</b>

                            <span class="mu">
                                cust_id
                                ${esc(x.src.cust_id)}
                            </span>

                            ${x.errs
                        .map(
                            (e) =>
                                `<span class="b er">
                                            ${e.field}
                                        </span>`
                        )
                        .join('')}

                            <span class="mu">
                                ${o
                        ? '▾'
                        : '▸'} evidence
                            </span>
                        </div>

                        ${o
                        ? `
                                    <div class="qb">
                                        ${x.errs
                            .map(
                                (e) =>
                                    `<p>
                                                        <b>${e.field}</b>
                                                        ←
                                                        <code>${e.source_field}</code>
                                                        =
                                                        <code>${esc(
                                        JSON.stringify(
                                            e.source_value
                                        )
                                    )}</code>
                                                        via
                                                        <code>${e.rule}</code>
                                                        <br>
                                                        <span class="er">
                                                            ✕
                                                            ${esc(
                                        e.message
                                    )}
                                                        </span>
                                                    </p>`
                            )
                            .join('')}
                                    </div>
                                `
                        : ''
                    }
                    </div>
                `;
            })
            .join('');

        return (
            btn +
            `
            <div class="c">
                ${kp([
                [d.src, 'Source'],
                [d.tr, 'Transformed'],
                [a, 'Accepted', 'ok'],
                [r, 'Quarantined', 'er']
            ])}

                <div class="funnel">
                    <i
                        class="fa"
                        style="width:${(a / T) * 100}%"
                        onclick="S.f='acc';S.ef=null;render()"
                    >
                        ${a} accepted
                    </i>

                    <i
                        class="fr"
                        style="width:${(r / T) * 100}%"
                        onclick="S.f='rej';S.ef=null;render()"
                    >
                        ${r}
                    </i>
                </div>

                <div class="chips">
                    ${chip('All', 'all')}
                    ${chip('Accepted', 'acc')}
                    ${chip('Quarantined', 'rej')}

                    ${Object.entries(by)
                .map(([k, n]) =>
                    chip(
                        k + ' · ' + n,
                        'rej',
                        k
                    )
                )
                .join('')}
                </div>

                <small>
                    Result hash
                    <code>
                        ${hash(
                    JSON.stringify(d.acc) +
                    JSON.stringify(d.rej)
                )}
                    </code>
                    · click a row for field-level evidence
                </small>
            </div>

            ${f != 'acc'
                ? `
                        <div class="c">
                            <h2>Quarantine</h2>
                            ${rej || '<small>None</small>'}
                        </div>
                    `
                : ''
            }

            ${f != 'rej'
                ? `
                        <div class="c">
                            <h2>Accepted (transformed)</h2>

                            <div class="w">
                                <table>
                                    <tr>
                                        ${TGT.map(
                    (x) =>
                        `<th>${x[0]}</th>`
                ).join('')}
                                    </tr>

                                    ${d.acc
                    .map(
                        (x) =>
                            `<tr>
                                                    ${TGT.map(
                                (y) =>
                                    `<td>
                                                                ${esc(
                                        x.rec[
                                        y[0]
                                        ]
                                    )}
                                                            </td>`
                            ).join('')}
                                                </tr>`
                    )
                    .join('')}
                                </table>
                            </div>
                        </div>
                    `
                : ''
            }
        `
        );
    },

    exec: () => {
        const v = curV();

        if (!v) {
            return '<div class="c">Run the agent first.</div>';
        }

        const d = S.dry[v.v];
        const st = lastRunStatus(v.v);
        const ready =
            v.status == 'approved' &&
            d &&
            d.hash == v.hash;

        const done = st == 'complete';

        const RC = S.recon[v.v] || {};
        const tk = {
            length: RC.in_target || 0
        };

        const sumS = RC.sum_expected || 0;
        const sumT = RC.sum_target || 0;

        let h = `
            <div class="c">
                <h2>Execute v${v.v} → mock target</h2>

                ${ready
                ? ''
                : `<p class="wa">
                            Blocked:
                            ${v.status != 'approved'
                    ? 'plan is not approved'
                    : 'dry run missing or stale'
                }.
                        </p>`
            }

                <div class="row">
                    <button
                        class="p"
                        ${ready && !done ? '' : 'disabled'}
                        onclick="act('execute')"
                    >
                        ${st && st != 'rolled_back'
                ? 'Retry execution'
                : 'Execute'
            }
                    </button>

                    <label>
                        <input
                            type="checkbox"
                            ${S.fail ? 'checked' : ''}
                            onchange="S.fail=this.checked"
                        >
                        simulate interruption after 10 inserts
                    </label>

                    <button
                        ${tk.length ? '' : 'disabled'}
                        onclick="act('rollback')"
                    >
                        Rollback v${v.v}
                    </button>
                </div>

                <p class="mu">
                    Idempotency: records are keyed by primary key;
                    a retry skips rows already present, so nothing
                    is inserted twice.
                </p>
            </div>
        `;

        if (!d) {
            return h;
        }

        const rows = [
            ['Source records', d.src, d.src],
            [
                'Accepted (expected in target)',
                d.acc.length,
                tk.length
            ],
            [
                'Quarantined (not migrated)',
                d.rej.length,
                0
            ],
            [
                'Sum of balance_cents',
                sumS,
                sumT
            ]
        ];

        const match =
            tk.length == d.acc.length &&
            sumS == sumT;

        return (
            h +
            `
            <div class="c">
                <h2>Reconciliation</h2>

                ${kp([
                [d.src, 'Source'],
                [
                    d.acc.length + d.rej.length,
                    'Accepted + quarantined',
                    d.acc.length + d.rej.length == d.src
                        ? 'ok'
                        : 'er'
                ],
                [tk.length, 'In target'],
                [
                    match
                        ? 'MATCH'
                        : tk.length
                            ? 'MISMATCH'
                            : 'EMPTY',
                    'Target vs accepted',
                    match ? 'ok' : 'er'
                ]
            ])}

                <div class="w">
                    <table>
                        <tr>
                            <th>Check</th>
                            <th>Expected</th>
                            <th>Target</th>
                        </tr>

                        <tr>
                            <td>Accepted count</td>
                            <td>${d.acc.length}</td>
                            <td>${tk.length}</td>
                        </tr>

                        <tr>
                            <td>Balance sum (cents)</td>
                            <td>${sumS}</td>
                            <td>${sumT}</td>
                        </tr>

                        <tr>
                            <td>Source = accepted + quarantined</td>
                            <td>${d.src}</td>
                            <td>
                                ${d.acc.length + d.rej.length}
                            </td>
                        </tr>
                    </table>
                </div>
            </div>

            <div class="c">
                <h2>Runs</h2>

                <div class="w">
                    <table>
                        <tr>
                            <th>Run</th>
                            <th>Plan</th>
                            <th>Attempt</th>
                            <th>Inserted</th>
                            <th>Skipped (dup)</th>
                            <th>Status</th>
                        </tr>

                        ${S.runs
                .map(
                    (r) =>
                        `<tr>
                                        <td>${r.id}</td>
                                        <td>v${r.v}</td>
                                        <td>${r.attempt || '—'}</td>
                                        <td>${r.ins}</td>
                                        <td>${r.skip}</td>
                                        <td>${r.status}</td>
                                    </tr>`
                )
                .join('')}
                    </table>
                </div>
            </div>
        `
        );
    },

    hist: () => `
        <div class="c">
            <h2>Audit history</h2>

            <div class="w">
                <table>
                    <tr>
                        <th>Time</th>
                        <th>Event</th>
                        <th>Detail</th>
                    </tr>

                    ${S.hist
            .map(
                (h) =>
                    `<tr>
                                        <td>${h.t}</td>
                                        <td>
                                            <span class="b">
                                                ${h.type}
                                            </span>
                                        </td>
                                        <td>${esc(h.d)}</td>
                                    </tr>`
            )
            .join('') ||
        '<tr><td colspan=3 class="mu">No events yet</td></tr>'
        }
                </table>
            </div>
        </div>
    `
};

$('v').innerHTML =
    '<div class="c mu">Loading…</div>';

load()
    .then(render)
    .catch(() => {
        $('v').innerHTML =
            '<div class="c er">Could not reach the backend. Check the server is running, then reload.</div>';
    });