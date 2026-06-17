package com.example.jokeapplication.controller;

import com.example.jokeapplication.entity.Joke;
import com.example.jokeapplication.service.JokeService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/jokes")
@RequiredArgsConstructor
public class JokeController {

    private final JokeService jokeService;

    @GetMapping
    public ResponseEntity<List<Joke>> getAllJokes() {
        return ResponseEntity.ok(jokeService.findAll());
    }

    @GetMapping("/random")
    public ResponseEntity<Joke> getRandomJoke() {
        return ResponseEntity.ok(jokeService.findRandom());
    }
}
