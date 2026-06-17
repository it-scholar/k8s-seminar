/**
 * @returns {Promise<{id: number, setup: string, punchline: string}>}
 */
export async function getRandomJoke() {
	const res = await fetch('/api/jokes/random');
	if (!res.ok) throw new Error(`Server error: ${res.status}`);
	return res.json();
}

/**
 * @returns {Promise<Array<{id: number, setup: string, punchline: string}>>}
 */
export async function getAllJokes() {
	const res = await fetch('/api/jokes');
	if (!res.ok) throw new Error(`Server error: ${res.status}`);
	return res.json();
}
