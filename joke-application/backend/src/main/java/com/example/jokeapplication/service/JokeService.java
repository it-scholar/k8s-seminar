package com.example.jokeapplication.service;

import com.example.jokeapplication.entity.Joke;
import com.example.jokeapplication.repository.JokeRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.NoSuchElementException;

@Service
@RequiredArgsConstructor
public class JokeService {

    private final JokeRepository jokeRepository;

    public List<Joke> findAll() {
        return jokeRepository.findAll();
    }

    public Joke findRandom() {
        return jokeRepository.findRandom()
                .orElseThrow(() -> new NoSuchElementException("No jokes found in the database."));
    }
}
