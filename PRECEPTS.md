# The Precepts

> The first utterance of the Junzi Harness. Before any agent acts, the harness opens with a
> fixed precept and then a rotating *precept of the day* from the classical Chinese canon —
> the Analects and the classics. A word to steady the mind before the work.

## The opening precept (fixed)

```text
"Is it not pleasant to learn with a constant perseverance and application?"
                                                    — Confucius, Analects
```

## The rotating precept

Beneath the fixed opening, the harness prints one rotating line from [`precepts.txt`](precepts.txt)
— a precept of the day, changing daily. The opening never changes; the precept rotates. Edit
[`precepts.txt`](precepts.txt) (one `Precept — Author` per line) to curate or extend the pool.
Twelve of the thirteen lines below are James Legge's renderings (Legge died 1897; his
translations of the Analects, Mencius, the Tao Te Ching and Zhuangzi are public domain
worldwide). The exception is the Xunzi line — Legge never translated Xunzi, and this
line's translator is **not identified**; its public-domain status is therefore unverified
and it should be re-sourced or replaced before anyone relies on it. The current pool:

> - *Learning without thought is labour lost; thought without learning is perilous.* — Confucius, Analects
> - *Is it not pleasant to learn with a constant perseverance and application?* — Confucius, Analects
> - *They who know the truth are not equal to those who love it, and they who love it are not equal to those who delight in it.* — Confucius, Analects
> - *When we see men of worth, we should think of equalling them; when we see men of a contrary character, we should turn inwards and examine ourselves.* — Confucius, Analects
> - *When I walk along with two others, they may serve me as my teachers. I will select their good qualities and follow them, their bad qualities and avoid them.* — Confucius, Analects
> - *To have faults and not to reform them: this, indeed, should be pronounced having faults.* — Confucius, Analects
> - *The commander of the forces of a large state may be carried off, but the will of even a common man cannot be taken from him.* — Confucius, Analects
> - *The great man is he who does not lose his child's-heart.* — Mencius
> - *The great end of learning is nothing else but to seek for the lost mind.* — Mencius
> - *He who knows other men is discerning; he who knows himself is intelligent. He who overcomes others is strong; he who overcomes himself is mighty.* — Laozi, Tao Te Ching
> - *The journey of a thousand li commenced with a single step.* — Laozi, Tao Te Ching
> - *There is a limit to our life, but to knowledge there is no limit.* — Zhuangzi
> - *The superior man says: Study should never stop.* — Xunzi

*The harness emits this first, on startup — [`bin/precept`](bin/precept).*
