<script>
	import { getAllJokes } from '$lib/api.js';
	import { onMount } from 'svelte';

	/** @type {Array<{id: number, setup: string, punchline: string}>} */
	let jokes = [];
	let loading = true;
	let error = null;

	onMount(async () => {
		try {
			jokes = await getAllJokes();
		} catch (e) {
			error = 'Could not reach the joke server. Make sure the backend is running!';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>All Jokes — JokeBox 😂</title>
</svelte:head>

<div class="min-h-screen bg-base-100 px-6 py-10 md:px-10">
	<div class="max-w-6xl mx-auto space-y-8">

		<!-- Header -->
		<header class="flex flex-col sm:flex-row sm:items-center gap-4">
			<a href="/" class="btn btn-ghost btn-sm self-start gap-1">
				← Back to Home
			</a>
			<div>
				<h1 class="text-4xl font-extrabold text-primary">📚 All Jokes</h1>
				{#if !loading && !error}
					<p class="text-base-content/50 mt-1">
						{jokes.length} joke{jokes.length === 1 ? '' : 's'} in the vault
					</p>
				{/if}
			</div>
		</header>

		<!-- Loading -->
		{#if loading}
			<div class="flex flex-col items-center justify-center py-32 gap-4">
				<span class="loading loading-spinner loading-lg text-primary"></span>
				<p class="text-base-content/40 text-sm">Fetching the goods…</p>
			</div>

		<!-- Error -->
		{:else if error}
			<div class="alert alert-error shadow-lg">
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

		<!-- Jokes Grid -->
		{:else}
			<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
				{#each jokes as joke (joke.id)}
					<div
						class="card bg-base-200 border border-base-300 shadow-md
						       hover:shadow-primary/20 hover:border-primary/40
						       transition-all duration-200 hover:-translate-y-0.5"
					>
						<div class="card-body gap-3 p-5">
							<div class="flex items-center justify-between">
								<div class="badge badge-primary badge-outline badge-sm font-mono">
									#{joke.id}
								</div>
							</div>

							<p class="font-semibold text-base-content leading-snug text-sm">
								{joke.setup}
							</p>

							<div class="divider my-0 text-xs text-base-content/20 uppercase tracking-widest">
								answer
							</div>

							<p class="text-accent italic text-sm leading-snug">
								{joke.punchline}
							</p>
						</div>
					</div>
				{/each}
			</div>
		{/if}

	</div>
</div>
