<script>
	import { getRandomJoke } from '$lib/api.js';

	/** @type {{ id: number, setup: string, punchline: string } | null} */
	let joke = null;
	let loading = false;
	let error = null;
	let revealed = false;

	async function fetchJoke() {
		loading = true;
		error = null;
		revealed = false;
		joke = null;
		try {
			joke = await getRandomJoke();
		} catch (e) {
			error = 'Could not reach the joke server. Make sure the backend is running!';
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>JokeBox 😂</title>
</svelte:head>

<div class="hero min-h-screen bg-base-100">
	<div class="hero-content flex-col gap-10 w-full max-w-2xl py-16">

		<!-- Header -->
		<div class="text-center space-y-3">
			<div class="text-8xl drop-shadow-lg select-none">😂</div>
			<h1 class="text-6xl font-extrabold text-primary tracking-tight">JokeBox</h1>
			<p class="text-base-content/50 text-lg">Your daily dose of terrible jokes</p>
		</div>

		<!-- CTA Button -->
		<button
			class="btn btn-primary btn-lg w-full max-w-xs text-lg shadow-lg shadow-primary/30 hover:scale-105 transition-transform"
			class:loading
			disabled={loading}
			on:click={fetchJoke}
		>
			{#if !loading}
				<span class="mr-1">🎲</span>
			{/if}
			{loading ? 'Loading...' : 'Get a Random Joke'}
		</button>

		<!-- Error -->
		{#if error}
			<div class="alert alert-error shadow-lg w-full">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					class="stroke-current shrink-0 h-6 w-6"
					fill="none"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
					/>
				</svg>
				<span>{error}</span>
			</div>
		{/if}

		<!-- Joke Card -->
		{#if joke}
			<div
				class="card bg-base-200 w-full shadow-2xl border border-base-300 hover:border-primary/30 transition-colors duration-300"
			>
				<div class="card-body gap-5">
					<!-- Setup -->
					<div class="flex gap-4 items-start">
						<span class="text-4xl mt-1 shrink-0">🤔</span>
						<p class="text-xl font-semibold leading-relaxed text-base-content">
							{joke.setup}
						</p>
					</div>

					<!-- Reveal button or punchline -->
					{#if !revealed}
						<button
							class="btn btn-outline btn-accent w-full gap-2 hover:scale-[1.02] transition-transform"
							on:click={() => (revealed = true)}
						>
							<span>🥁</span> Reveal the Punchline
						</button>
					{:else}
						<div class="divider text-base-content/30 text-xs uppercase tracking-widest">
							punchline
						</div>
						<div class="flex gap-4 items-start animate-pulse-once">
							<span class="text-4xl mt-1 shrink-0">😆</span>
							<p class="text-xl font-medium text-accent italic leading-relaxed">
								{joke.punchline}
							</p>
						</div>
					{/if}
				</div>
			</div>
		{/if}

		<!-- Browse all -->
		<a
			href="/jokes"
			class="btn btn-ghost btn-sm gap-2 text-base-content/60 hover:text-secondary transition-colors"
		>
			📚 Browse all jokes →
		</a>
	</div>
</div>
